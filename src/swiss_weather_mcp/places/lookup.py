"""
Find the swisstopo places a user means by the name they type.

    "Wil" ──► normalise ──► "wil" ──► lookup_name_to_places ──► [Wil SG, Wil (near Dübendorf), Wil ZH, ...]

A lookup name is a name a place can be found by, normalised, such as "wil sg" and "wil" for Wil SG,
or "geneva" for Genève.

The places are built once per process from the rows of swissNAMES3D:
1. LOOKUP NAMES: each row is filed under every lookup name a user may type. Case, accents and periods
   do not matter, a canton after the name may be left out, and each part of a settlement's name
   with a slash counts on its own:
       "St. Moritz"  → "st moritz"
       "Wil SG"      → "wil sg", "wil"
       "Biel/Bienne" → "biel/bienne", "biel", "bienne"
2. GROUPS: rows under the same lookup name that lie within 2 km of each other are one place, such as
   a lake and the quarter on its shore. The place has the official name, position and
   altitude of its biggest settlement, else of its main landmark, and all object types.
3. ORDER: the places of a lookup name are sorted biggest first, so a list of them starts with the
   towns a user most likely means.
4. ENGLISH: english_place_names.csv adds the English names swissNAMES3D lacks, such as Geneva
   for Genève, each under a lookup name no row has.
5. SETTLEMENTS: the settlements with people, each once under its official name, the biggest first, to
   find the biggest settlements inside a municipality, such as Davos Dorf and Davos Platz in Davos.

A name no lookup name matches gets suggestions, see suggestions.py.
"""
import csv
import logging
import re
import threading
import unicodedata
from collections import defaultdict
from importlib import resources

from ..cantons import SWISS_CANTON_ABBREVIATIONS
from ..distance import calculate_distance_m
from ..polygons import Polygon, find_bounding_box, polygons_contain
from .place_source import OBJECT_TYPE_TO_NAME, SETTLEMENT_TYPES, SwisstopoPlace, SwisstopoPlaceSource
from .suggestions import find_similar_places


logger = logging.getLogger(__name__)

# Rows of the same lookup name this close together are one place
SAME_PLACE_RADIUS_M = 2000
# A name such as "Wil SG", the canton telling it apart from the other places called Wil
CANTON_SUFFIX = re.compile(rf"^(.*\S) (?:{'|'.join(SWISS_CANTON_ABBREVIATIONS)})$")
# A settlement this big or bigger is named as near: 100 people or more
MIN_NEAR_SETTLEMENT_BAND = 4
# The order in which the types of a place are named, settlements first
OBJECT_TYPE_TO_RANK = {object_type: rank for rank, object_type in enumerate(OBJECT_TYPE_TO_NAME)}
ENGLISH_PLACE_NAMES_FILE = "english_place_names.csv"


def normalise_name(name: str) -> str:
    """Fold case, strip accents and drop periods, so "zurich" finds "Zürich" and "St Moritz" finds "St. Moritz"."""
    # NFKD splits "ü" into "u" and a separate accent mark, which is dropped here
    casefolded = name.casefold()
    decomposed = unicodedata.normalize("NFKD", casefolded)
    without_accents = ""
    for character in decomposed:
        if not unicodedata.combining(character):
            without_accents += character
    # A period becomes a space, so "i.E." and "i. E." are the same once the words are joined by one space
    words = without_accents.replace(".", " ").split()
    return " ".join(words)


def _build_lookup_names(row: SwisstopoPlace) -> list[str]:
    """Build every lookup name of a row, such as "wil sg" and "wil" for "Wil SG"."""
    lookup_names = [normalise_name(row.name)]
    canton_suffix = CANTON_SUFFIX.match(row.name)
    if canton_suffix:
        name_without_canton = canton_suffix.group(1)
        lookup_name_without_canton = normalise_name(name_without_canton)
        lookup_names.append(lookup_name_without_canton)
    # Only settlements: other slashes split names such as the shooting range "Küsnacht 300/50/25m"
    if "/" in row.name and row.object_types[0] in SETTLEMENT_TYPES:
        for part in row.name.split("/"):
            if part.strip():
                lookup_name_of_part = normalise_name(part)
                lookup_names.append(lookup_name_of_part)
    # A lookup name counts once, even when two rules give it
    unique_lookup_names = list(dict.fromkeys(lookup_names))
    return unique_lookup_names


def _group_close_rows(rows: list[SwisstopoPlace]) -> list[list[SwisstopoPlace]]:
    """Group rows of the same lookup name: a row joins every group with a member within SAME_PLACE_RADIUS_M."""
    groups: list[list[SwisstopoPlace]] = []
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


def _calculate_group_size(group: list[SwisstopoPlace]) -> tuple[int, int]:
    """Return how big a group of rows is, to sort by: the band of its biggest settlement, then its number of rows."""
    population_band = 0
    for row in group:
        population_band = max(population_band, row.population_band)
    return population_band, len(group)


def _merge_rows(group: list[SwisstopoPlace]) -> SwisstopoPlace:
    """
    Merge a group of rows into one place.

    The place is named and placed after its biggest settlement, else after its main landmark. An
    official name wins over an exonym in the same spot, so Zürich is not shown as Zurich.
    """
    settlements = [row for row in group if row.object_types[0] in SETTLEMENT_TYPES]
    if settlements:
        main_row = max(settlements, key=lambda row: (row.population_band, not row.is_exonym))
    else:
        main_row = min(group, key=lambda row: (OBJECT_TYPE_TO_RANK[row.object_types[0]], row.is_exonym))

    types_in_group = set()
    for row in group:
        types_in_group.add(row.object_types[0])
    # OBJECT_TYPE_TO_NAME lists the types in the order a place names them, settlements first
    object_types = []
    for object_type in OBJECT_TYPE_TO_NAME:
        if object_type in types_in_group:
            object_types.append(object_type)
    ordered_object_types = tuple(object_types)
    return main_row._replace(object_types=ordered_object_types)


def _build_places_of_lookup_name(rows: list[SwisstopoPlace]) -> list[SwisstopoPlace]:
    """Turn the rows of one lookup name into its places, biggest first: most people, then most rows."""
    groups = _group_close_rows(rows)
    groups.sort(key=_calculate_group_size, reverse=True)
    places = []
    for group in groups:
        place = _merge_rows(group)
        places.append(place)
    return places


def _read_english_place_names() -> dict[str, str]:
    """Map each English name of the shipped file to the name swissNAMES3D gives its place."""
    names_file = resources.files("swiss_weather_mcp.places").joinpath(ENGLISH_PLACE_NAMES_FILE)
    text = names_file.read_text(encoding="utf-8")
    # The file starts with comment lines that say why it exists
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    english_name_to_swissnames3d_name = {}
    for row in csv.DictReader(lines, delimiter=";"):
        english_name_to_swissnames3d_name[row["english_name"]] = row["swissnames3d_name"]
    return english_name_to_swissnames3d_name


def _add_english_names(lookup_name_to_places: dict[str, list[SwisstopoPlace]]) -> None:
    """File the places of each English name under its lookup name too, unless a row of swissNAMES3D has it."""
    english_name_to_swissnames3d_name = _read_english_place_names()
    for english_name, swissnames3d_name in english_name_to_swissnames3d_name.items():
        english_lookup_name = normalise_name(english_name)
        swissnames3d_lookup_name = normalise_name(swissnames3d_name)
        if english_lookup_name in lookup_name_to_places:
            continue
        if swissnames3d_lookup_name not in lookup_name_to_places:
            logger.warning(f"No place is called {swissnames3d_name}, so the English name {english_name} finds nothing")
            continue
        lookup_name_to_places[english_lookup_name] = lookup_name_to_places[swissnames3d_lookup_name]


def _find_near_settlements(lookup_name_to_places: dict[str, list[SwisstopoPlace]]) -> list[SwisstopoPlace]:
    """Find the settlements of 100 or more people whose official name belongs to no other place."""
    near_settlements = []
    for lookup_name, places in lookup_name_to_places.items():
        place = places[0]
        # Under its own name, not a shorter lookup name such as "wil" for "Wil SG"
        is_named_once = len(places) == 1 and normalise_name(place.name) == lookup_name
        # The official name, so Genève is named and not Genf
        is_official_settlement = place.object_types[0] in SETTLEMENT_TYPES and not place.is_exonym
        is_big_enough = place.population_band >= MIN_NEAR_SETTLEMENT_BAND
        if is_named_once and is_official_settlement and is_big_enough:
            near_settlements.append(place)
    return near_settlements


def _list_settlements(lookup_name_to_places: dict[str, list[SwisstopoPlace]]) -> list[SwisstopoPlace]:
    """List each settlement with people once, under its official name: the biggest first, then by name."""
    settlements = []
    settlement_positions = set()
    for places in lookup_name_to_places.values():
        for place in places:
            # The official name, so Brig is listed and not Briga or Brigue in the same spot
            is_official_settlement = place.object_types[0] in SETTLEMENT_TYPES and not place.is_exonym
            # A place is filed under several lookup names, such as Biel/Bienne under "biel" and "bienne"
            position = (place.east_m, place.north_m)
            if is_official_settlement and place.population_band and position not in settlement_positions:
                settlement_positions.add(position)
                settlements.append(place)
    # By the normalised name, so an accent does not move a name to the end, such as Évolène after Zinal
    settlements.sort(key=lambda settlement: (-settlement.population_band, normalise_name(settlement.name)))
    return settlements


class SwisstopoPlaceLookup:
    """Find the swisstopo places of a lookup name, and the places to suggest for a name no lookup name matches."""

    def __init__(self, place_source: SwisstopoPlaceSource):
        self.place_source = place_source
        self._lookup_name_to_places: dict[str, list[SwisstopoPlace]] = {}
        self._near_settlements: list[SwisstopoPlace] = []
        self._settlements: list[SwisstopoPlace] = []
        # The load at server start and a request may come at the same time, and only one may download
        self._loading_lock = threading.Lock()

    def load_places(self) -> None:
        """Read the rows, file them under their lookup names and merge close rows into places, once per process."""
        with self._loading_lock:
            if self._lookup_name_to_places:
                return

            lookup_name_to_rows: dict[str, list[SwisstopoPlace]] = defaultdict(list)
            for row in self.place_source.read_rows():
                for lookup_name in _build_lookup_names(row):
                    lookup_name_to_rows[lookup_name].append(row)

            lookup_name_to_places = {}
            for lookup_name, rows in lookup_name_to_rows.items():
                lookup_name_to_places[lookup_name] = _build_places_of_lookup_name(rows)
            _add_english_names(lookup_name_to_places)

            self._near_settlements = _find_near_settlements(lookup_name_to_places)
            self._settlements = _list_settlements(lookup_name_to_places)
            # Published last, so a parallel request never sees part of the places
            self._lookup_name_to_places = lookup_name_to_places
            logger.info(f"Built the places of {len(lookup_name_to_places)} lookup names")

    @property
    def near_settlements(self) -> list[SwisstopoPlace]:
        """The settlements of 100 or more people with a unique official name, to name as near."""
        self.load_places()
        return self._near_settlements

    def find_places_named(self, location: str) -> list[SwisstopoPlace]:
        """Find the places a user means by a name, biggest first; none for a name no lookup name matches."""
        self.load_places()
        lookup_name = normalise_name(location)
        places = self._lookup_name_to_places.get(lookup_name, [])
        # A copy, so a caller cannot change the stored list
        return list(places)

    def find_places_named_alike(self, location: str) -> list[SwisstopoPlace]:
        """Find the places to suggest for a name no lookup name matches, see suggestions.py."""
        self.load_places()
        query = normalise_name(location)
        return find_similar_places(self._lookup_name_to_places, query)

    def find_biggest_settlements_inside(self, polygons: list[Polygon]) -> list[SwisstopoPlace]:
        """
        Find the biggest settlements inside polygons, sorted by name: those of the biggest population band there.

        Return none when no settlement with people lies inside.
        """
        self.load_places()
        bounding_box = find_bounding_box(polygons)
        biggest_settlements: list[SwisstopoPlace] = []
        for settlement in self._settlements:
            # The settlements come biggest first, so the first one inside sets the band
            if biggest_settlements and settlement.population_band < biggest_settlements[0].population_band:
                break
            is_in_box = bounding_box.contains(settlement.east_m, settlement.north_m)
            if is_in_box and polygons_contain(polygons, settlement.east_m, settlement.north_m):
                biggest_settlements.append(settlement)
        return biggest_settlements
