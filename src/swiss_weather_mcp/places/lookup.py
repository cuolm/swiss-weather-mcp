"""
Find the swisstopo places a user means by the name they type.

    "Wil" ──► normalise ──► "wil" ──► places_by_name ──► [Wil SG, Wil (near Dübendorf), Wil ZH, ...]

The places are built once per process from the rows of swissNAMES3D:
1. KEYS: each row is filed under every name a user may type. Case, accents and periods do not
   matter, a canton after the name may be left out, and each part of a settlement's name with
   a slash counts on its own:
       "St. Moritz"  → "st moritz"
       "Wil SG"      → "wil sg", "wil"
       "Biel/Bienne" → "biel/bienne", "biel", "bienne"
2. GROUPS: rows under the same key that lie within 2 km of each other are one place, such as
   a lake and the district on its shore. The place has the official name, position and
   altitude of its biggest settlement, else of its main landmark, and all object types.
3. ORDER: the places of a key are sorted biggest first, so a list of them starts with the
   towns a user most likely means.
4. ENGLISH: english_place_names.csv adds the English names swissNAMES3D lacks, such as Geneva
   for Genève, each under a key no row has.

A name no row has, such as "Davos", finds the names that start with it, Davos Dorf and Davos
Platz, and the names spelled alike, such as Zürich for "Zuerich".
"""
import bisect
import csv
import difflib
import logging
import math
import re
import threading
import unicodedata
from collections import defaultdict
from importlib import resources
from typing import Dict, List

from .source import OBJECT_TYPE_NAMES, SETTLEMENT_TYPES, SwisstopoPlace, SwisstopoPlaceSource

logger = logging.getLogger(__name__)

# Rows of the same name this close together are one place
SAME_PLACE_RADIUS_M = 2000
CANTON_ABBREVIATIONS = (
    "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE",
    "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH",
)
# A name such as "Wil SG", the canton telling it apart from the other places called Wil
CANTON_SUFFIX = re.compile(rf"^(.*\S) (?:{'|'.join(CANTON_ABBREVIATIONS)})$")
# What may follow a name at the start of a longer one, such as "Davos" in "Davos Dorf"
NAME_SEPARATORS = (" ", "-", "/", "(", ",")
# How alike two names must be to suggest one for the other, from difflib: 1 is the same name
SIMILAR_NAME_MIN_RATIO = 0.75
MAX_SIMILAR_NAMES = 5
# A settlement this big or bigger is named as near: 100 people or more
MIN_NEAR_SETTLEMENT_BAND = 4
# The order in which the types of a place are named, settlements first
OBJECT_TYPE_ORDER = {object_type: rank for rank, object_type in enumerate(OBJECT_TYPE_NAMES)}
ENGLISH_PLACE_NAMES_FILE = "english_place_names.csv"


def normalise_location(location: str) -> str:
    """Fold case, strip accents and drop periods, so "zurich" finds "Zürich" and "St Moritz" finds "St. Moritz"."""
    # NFKD splits "ü" into "u" and a separate accent mark, which is dropped here
    decomposed = unicodedata.normalize("NFKD", location.casefold())
    without_accents = ""
    for character in decomposed:
        if not unicodedata.combining(character):
            without_accents += character
    # A period becomes a space, so "i.E." and "i. E." are the same once the words are joined by one space
    words = without_accents.replace(".", " ").split()
    return " ".join(words)


def calculate_distance_m(first: SwisstopoPlace, second: SwisstopoPlace) -> float:
    """Return the distance in metres between two places."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(first.east_m - second.east_m, first.north_m - second.north_m)


def _find_keys(row: SwisstopoPlace) -> List[str]:
    """Return every name a user may type for a row, such as "wil sg" and "wil" for "Wil SG"."""
    keys = [normalise_location(row.name)]
    canton_suffix = CANTON_SUFFIX.match(row.name)
    if canton_suffix:
        keys.append(normalise_location(canton_suffix.group(1)))
    # Only settlements: other slashes split names such as the shooting range "Küsnacht 300/50/25m"
    if "/" in row.name and row.object_types[0] in SETTLEMENT_TYPES:
        for part in row.name.split("/"):
            if part.strip():
                keys.append(normalise_location(part))
    # A key counts once, even when two rules give it
    return list(dict.fromkeys(keys))


def _group_close_rows(rows: List[SwisstopoPlace]) -> List[List[SwisstopoPlace]]:
    """Group rows of the same key: a row joins every group with a member within SAME_PLACE_RADIUS_M."""
    groups: List[List[SwisstopoPlace]] = []
    for row in rows:
        touching_groups = []
        for group in groups:
            for member in group:
                if calculate_distance_m(row, member) <= SAME_PLACE_RADIUS_M:
                    touching_groups.append(group)
                    break
        merged_group = [row]
        for group in touching_groups:
            merged_group.extend(group)
            groups.remove(group)
        groups.append(merged_group)
    return groups


def _merge_rows(group: List[SwisstopoPlace]) -> SwisstopoPlace:
    """
    Merge a group of rows into one place, named and placed after its biggest settlement, else
    after its main landmark. An official name wins over an exonym in the same spot, so Zürich
    is not shown as Zurich.
    """
    settlements = [row for row in group if row.object_types[0] in SETTLEMENT_TYPES]
    if settlements:
        main_row = max(settlements, key=lambda row: (row.population_band, not row.is_exonym))
    else:
        main_row = min(group, key=lambda row: (OBJECT_TYPE_ORDER[row.object_types[0]], row.is_exonym))

    object_types = sorted({row.object_types[0] for row in group}, key=lambda object_type: OBJECT_TYPE_ORDER[object_type])
    return main_row._replace(object_types=tuple(object_types))


def _read_english_place_names() -> Dict[str, str]:
    """Map each English name of the shipped file to the name swissNAMES3D gives its place."""
    names_file = resources.files("swiss_weather_mcp.places").joinpath(ENGLISH_PLACE_NAMES_FILE)
    text = names_file.read_text(encoding="utf-8")
    # The file starts with comment lines that say why it exists
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    swissnames3d_names_by_english_name = {}
    for row in csv.DictReader(lines, delimiter=";"):
        swissnames3d_names_by_english_name[row["english_name"]] = row["swissnames3d_name"]
    return swissnames3d_names_by_english_name


def _add_english_names(places_by_name: Dict[str, List[SwisstopoPlace]]) -> None:
    """File the places of each English name under it too, unless a row of swissNAMES3D has that name."""
    for english_name, swissnames3d_name in _read_english_place_names().items():
        english_key = normalise_location(english_name)
        swissnames3d_key = normalise_location(swissnames3d_name)
        if english_key in places_by_name:
            continue
        if swissnames3d_key not in places_by_name:
            logger.warning(f"No place is called {swissnames3d_name}, so the English name {english_name} finds nothing")
            continue
        places_by_name[english_key] = places_by_name[swissnames3d_key]


def _build_places(rows: List[SwisstopoPlace]) -> List[SwisstopoPlace]:
    """Turn the rows of one key into its places, biggest first: most people, then most rows."""
    sized_places = []
    for group in _group_close_rows(rows):
        sized_places.append((_merge_rows(group), len(group)))
    sized_places.sort(key=lambda sized_place: (sized_place[0].population_band, sized_place[1]), reverse=True)
    return [place for place, _ in sized_places]


class SwisstopoPlaceLookup:
    """Find the swisstopo places with a name, and the names that start with it or are spelled alike."""

    def __init__(self, place_source: SwisstopoPlaceSource):
        self.place_source = place_source
        self._places_by_name: Dict[str, List[SwisstopoPlace]] = {}
        # The keys of _places_by_name, sorted, to find the ones that start with a name
        self._names: List[str] = []
        self._near_settlements: List[SwisstopoPlace] = []
        # The load at server start and a request may come at the same time, and only one may download
        self._loading_lock = threading.Lock()

    def load_places(self) -> None:
        """Read the rows, file them under their keys and merge close rows into places, once per process."""
        with self._loading_lock:
            if self._places_by_name:
                return

            rows_by_key: Dict[str, List[SwisstopoPlace]] = defaultdict(list)
            for row in self.place_source.load_places():
                for key in _find_keys(row):
                    rows_by_key[key].append(row)

            places_by_name = {}
            for key, rows in rows_by_key.items():
                places_by_name[key] = _build_places(rows)
            _add_english_names(places_by_name)

            near_settlements = []
            for key, places in places_by_name.items():
                place = places[0]
                # Under its own name, not a shorter key such as "wil" for "Wil SG"
                is_named_once = len(places) == 1 and normalise_location(place.name) == key
                # The official name, so Genève is named and not Genf
                is_official_settlement = place.object_types[0] in SETTLEMENT_TYPES and not place.is_exonym
                if is_named_once and is_official_settlement and place.population_band >= MIN_NEAR_SETTLEMENT_BAND:
                    near_settlements.append(place)

            self._names = sorted(places_by_name)
            self._near_settlements = near_settlements
            # Published last, so a parallel request never sees part of the places
            self._places_by_name = places_by_name
            logger.info(f"Built the places of {len(places_by_name)} names")

    @property
    def near_settlements(self) -> List[SwisstopoPlace]:
        """
        The settlements of 100 or more people whose official name belongs to no other place,
        to name as near.
        """
        self.load_places()
        return self._near_settlements

    def find_places(self, location: str) -> List[SwisstopoPlace]:
        """Find the places a user means by a name, biggest first; none for a name no row has."""
        self.load_places()
        return list(self._places_by_name.get(normalise_location(location), []))

    def _find_names_starting_with(self, typed_name: str) -> List[str]:
        """Find the names that start with a typed name as a whole word, such as "davos dorf" for "davos"."""
        names = []
        index = bisect.bisect_left(self._names, typed_name)
        while index < len(self._names) and self._names[index].startswith(typed_name):
            name = self._names[index]
            if len(name) > len(typed_name) and name[len(typed_name)] in NAME_SEPARATORS:
                names.append(name)
            index += 1
        return names

    def _find_biggest_places(self, names: List[str]) -> List[SwisstopoPlace]:
        """Return the biggest place of each name, biggest first."""
        places = [self._places_by_name[name][0] for name in names]
        return sorted(places, key=lambda place: place.population_band, reverse=True)

    def find_similar_names(self, location: str) -> List[SwisstopoPlace]:
        """
        Find the places to suggest when no row has a name, at most MAX_SIMILAR_NAMES: first the
        names that start with it, then the names spelled alike, each biggest first. So "Flims"
        suggests Flims Dorf before Flums, a bigger town spelled alike.
        """
        self.load_places()
        typed_name = normalise_location(location)
        starting_names = self._find_names_starting_with(typed_name)
        alike_names = difflib.get_close_matches(typed_name, self._names, n=MAX_SIMILAR_NAMES, cutoff=SIMILAR_NAME_MIN_RATIO)

        suggestions: List[SwisstopoPlace] = []
        for place in self._find_biggest_places(starting_names) + self._find_biggest_places(alike_names):
            # A place may be under two names, such as Biel/Bienne under "biel" and "bienne"
            if place not in suggestions:
                suggestions.append(place)
        return suggestions[:MAX_SIMILAR_NAMES]
