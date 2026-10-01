"""
Find the MeteoSwiss point for a place name or a Swiss postal code.

What it looks a location up in:
- The MeteoSwiss point table, see meteoswiss_points.py. Names and postal codes are not
  unique in it. For example, 24 points are called Zürich, and 12 share the postal code 1945.
- other_language_place_names.csv, which ships with the package and is built from
  Wikipedia: 349 names in Romansh, French, German, Italian and English, such as Genf,
  Ginevra and Geneva for Genève.
- The swisstopo places, see swisstopo_places.py, for a name that is not a MeteoSwiss point.
  About 12 % of their names belong to several places, such as the seven Blausee.

How we use it:
1. On the first question, read the point table and keep it in memory for as long as
   the process runs.
2. Sort the points by rank and keep the first one per postal code and per name:
   postal code centers first, then the lowest postal code, then the lowest point ID.

    "Zürich", 24 points                 "Säntis", 2 points
      800100  Zürich 8001   kept          35   station             kept
      800200  Zürich 8002                 711  point of interest
      ...                                 (no postal codes, lowest ID wins)

3. Ignore case and accents, then look the place up:

    "Genf"  ──>  "genf"
      1. a postal code?               no
      2. a MeteoSwiss name?           no
      3. a name in another language?  yes: "geneve", Genève 1201 (381 m)

4. Only when none of these finds the name, read the swisstopo places, once per process.
   Group the places that share a name and lie within 2 km of each other: they are one
   place, such as a hill and the hamlet on it, and the settlement stands for the group.
   A name of one group is answered for by the MeteoSwiss point nearest to it. A name of
   several groups is refused with one option per group, written so the lookup accepts it:

    "Blausee", 6 groups
      "Blausee (lake, 2205 m, near Bettmeralp)"     the nearest MeteoSwiss village
      "Blausee (district, 887 m, near Blausee-Mitholz)"
      ...

   Free text that starts with such a name, such as "Blausee near Bettmeralp", picks the
   option whose village it names.
"""
import csv
import logging
import math
import re
import unicodedata
from collections import defaultdict
from importlib import resources
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple, Union

from ..errors import CannotAnswerError
from .meteoswiss_points import MeteoSwissPoint, MeteoSwissPointSource
from .swisstopo_places import SETTLEMENT_OBJECT_TYPES, SwisstopoPlace, SwisstopoPlaceSource

logger = logging.getLogger(__name__)

OTHER_LANGUAGE_PLACE_NAMES_FILE = "other_language_place_names.csv"
# swisstopo places with the same name this close together are one place
SAME_PLACE_RADIUS_M = 2000


class ResolvedLocation(NamedTuple):
    """The MeteoSwiss point a location is answered for, and the swisstopo place when the location is not a point."""
    point: MeteoSwissPoint
    place: Optional[SwisstopoPlace]


def calculate_distance_m(first: Union[MeteoSwissPoint, SwisstopoPlace],
                         second: Union[MeteoSwissPoint, SwisstopoPlace]) -> float:
    """Return the distance in metres between two MeteoSwiss points or swisstopo places."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(first.east_m - second.east_m, first.north_m - second.north_m)


def _rank_point(point: MeteoSwissPoint) -> Tuple[bool, str, int]:
    """Sort key for points that share a postal code or name; the first one is used."""
    return (not point.postal_code, point.postal_code, int(point.point_id))


def _normalise_location(location: str) -> str:
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


def _choose_point_per_postal_code(ranked_points: List[MeteoSwissPoint]) -> Dict[str, MeteoSwissPoint]:
    """Map each postal code to its first ranked point."""
    chosen_point_by_postal_code: Dict[str, MeteoSwissPoint] = {}
    for point in ranked_points:
        if not point.postal_code:
            continue  # stations and points of interest have none
        if point.postal_code not in chosen_point_by_postal_code:
            chosen_point_by_postal_code[point.postal_code] = point
    return chosen_point_by_postal_code


def _choose_point_per_name(ranked_points: List[MeteoSwissPoint]) -> Dict[str, MeteoSwissPoint]:
    """Map each normalised name to its first ranked point."""
    chosen_point_by_name: Dict[str, MeteoSwissPoint] = {}
    for point in ranked_points:
        point_name = _normalise_location(point.name)
        if point_name not in chosen_point_by_name:
            chosen_point_by_name[point_name] = point
    return chosen_point_by_name


def _group_close_places(places: List[SwisstopoPlace]) -> List[List[SwisstopoPlace]]:
    """Group places that share a name: a place joins every group with a member within SAME_PLACE_RADIUS_M."""
    groups: List[List[SwisstopoPlace]] = []
    for place in places:
        touching_groups = []
        for group in groups:
            for member in group:
                if calculate_distance_m(place, member) <= SAME_PLACE_RADIUS_M:
                    touching_groups.append(group)
                    break
        merged_group = [place]
        for group in touching_groups:
            merged_group.extend(group)
            groups.remove(group)
        groups.append(merged_group)
    return groups


def _choose_place(group: List[SwisstopoPlace]) -> SwisstopoPlace:
    """Return the widest settlement of a group of places with the same name, or its first place."""
    for object_type in SETTLEMENT_OBJECT_TYPES:
        for place in group:
            if place.object_type == object_type:
                return place
    return group[0]


def _find_nearest_point(place: SwisstopoPlace, points: List[MeteoSwissPoint]) -> MeteoSwissPoint:
    """Find the point nearest to a place."""
    return min(points, key=lambda point: calculate_distance_m(point, place))


def _read_other_language_place_names() -> Dict[str, str]:
    """Map each other language place name, normalised, to its normalised MeteoSwiss point name."""
    package_files = resources.files("swiss_weather_mcp.locations")
    names_file = package_files.joinpath(OTHER_LANGUAGE_PLACE_NAMES_FILE)
    text = names_file.read_text(encoding="utf-8")
    # The file starts with comment lines that name its sources
    lines = [line for line in text.splitlines() if not line.startswith("#")]

    point_names_by_other_language_place_name = {}
    for row in csv.DictReader(lines, delimiter=";"):
        other_language_place_name = _normalise_location(row["other_language_place_name"])
        point_name = _normalise_location(row["meteoswiss_point_name"])
        point_names_by_other_language_place_name[other_language_place_name] = point_name
    return point_names_by_other_language_place_name


class LocationFinder:
    """Find the MeteoSwiss point for a name or a postal code, through the swisstopo places when it is not a point."""

    def __init__(self, cache_dir: Path):
        self.meteoswiss_point_source = MeteoSwissPointSource(cache_dir)
        self.swisstopo_place_source = SwisstopoPlaceSource(cache_dir)
        self._points: List[MeteoSwissPoint] = []
        # The points with a postal code, which name a village or town a reader knows
        self._villages: List[MeteoSwissPoint] = []
        self._place_groups_by_name: Dict[str, List[List[SwisstopoPlace]]] = {}
        self._point_count = 0
        self._chosen_point_by_postal_code: Dict[str, MeteoSwissPoint] = {}
        self._chosen_point_by_name: Dict[str, MeteoSwissPoint] = {}
        self._point_names_by_other_language_place_name = _read_other_language_place_names()

    def _ensure_chosen_points(self) -> None:
        """Read the point table and choose one point per postal code and per name, once per process."""
        if self._chosen_point_by_postal_code and self._chosen_point_by_name:
            return

        points = self.meteoswiss_point_source.load_points()
        ranked_points = sorted(points, key=_rank_point)
        self._points = points
        self._villages = [point for point in points if point.postal_code]
        self._point_count = len(points)
        self._chosen_point_by_postal_code = _choose_point_per_postal_code(ranked_points)
        self._chosen_point_by_name = _choose_point_per_name(ranked_points)
        logger.info(f"Loaded {len(points)} forecast locations")

    def _ensure_place_groups(self) -> None:
        """Read the swisstopo places and group them by name, once per process."""
        if self._place_groups_by_name:
            return

        places = self.swisstopo_place_source.load_places()
        places_by_name: Dict[str, List[SwisstopoPlace]] = defaultdict(list)
        for place in places:
            places_by_name[_normalise_location(place.name)].append(place)
        place_groups_by_name = {}
        for name, named_places in places_by_name.items():
            place_groups_by_name[name] = _group_close_places(named_places)
        # Published only once complete, so a parallel request never sees part of the groups
        self._place_groups_by_name = place_groups_by_name

    def _build_option(self, place: SwisstopoPlace) -> str:
        """Build the text that picks one of several places, such as "Blausee (lake, 2205 m, near Bettmeralp)"."""
        village = _find_nearest_point(place, self._villages)
        return f"{place.name} ({place.object_type_name}, {place.altitude_m:.0f} m, near {village.name})"

    def _build_options_error(self, groups: List[List[SwisstopoPlace]]) -> CannotAnswerError:
        """Build the refusal of a name of several places, with one option per place."""
        name = groups[0][0].name
        option_lines = []
        for group in groups:
            option = self._build_option(_choose_place(group))
            option_lines.append(f'  "{option}"')
        listed_options = "\n".join(option_lines)
        return CannotAnswerError(
            f"'{name}' is the name of {len(groups)} places. Ask again with one of these, written exactly as "
            f"shown:\n{listed_options}"
        )

    def _split_known_name(self, location: str) -> Tuple[Optional[str], str]:
        """Split free text into the longest place name it starts with and the rest, both normalised."""
        words = location.split()
        for word_count in range(len(words) - 1, 0, -1):
            name = _normalise_location(" ".join(words[:word_count]))
            if name in self._place_groups_by_name:
                rest = _normalise_location(" ".join(words[word_count:]))
                return name, rest
        return None, ""

    def _find_point_by_name(self, normalised_location: str) -> Optional[MeteoSwissPoint]:
        """Find the MeteoSwiss point for a normalised postal code, name, or name in another language."""
        if normalised_location in self._chosen_point_by_postal_code:
            return self._chosen_point_by_postal_code[normalised_location]

        if normalised_location in self._chosen_point_by_name:
            return self._chosen_point_by_name[normalised_location]

        if normalised_location in self._point_names_by_other_language_place_name:
            meteoswiss_name = self._point_names_by_other_language_place_name[normalised_location]
            # The names file was built from an older point table, so its place may be gone
            if meteoswiss_name in self._chosen_point_by_name:
                return self._chosen_point_by_name[meteoswiss_name]
        return None

    def _find_place(self, location: str) -> SwisstopoPlace:
        """
        Find the swisstopo place for a name, an option such as "Blausee (lake, 2205 m, near
        Bettmeralp)", or free text such as "Blausee near Bettmeralp".

        Raise CannotAnswerError for an unknown name, and for a name of several places with no
        option or village to pick one.
        """
        self._ensure_place_groups()
        normalised_location = _normalise_location(location)
        if normalised_location in self._place_groups_by_name:
            groups = self._place_groups_by_name[normalised_location]
            if len(groups) == 1:
                return _choose_place(groups[0])
            raise self._build_options_error(groups)

        name, rest = self._split_known_name(location)
        if name is None:
            raise CannotAnswerError(
                f"Location '{location}' is neither one of the {self._point_count} places MeteoSwiss publishes "
                f"forecasts for nor a place swisstopo names. Check the spelling, or try a nearby town, village "
                f"or postal code."
            )
        groups = self._place_groups_by_name[name]
        if len(groups) == 1:
            return _choose_place(groups[0])
        for group in groups:
            place = _choose_place(group)
            if normalised_location == _normalise_location(self._build_option(place)):
                return place
        for group in groups:
            place = _choose_place(group)
            village = _find_nearest_point(place, self._villages)
            # Whole words only, so a village such as Au is not found inside another word
            if re.search(rf"\b{re.escape(_normalise_location(village.name))}\b", rest):
                return place
        raise self._build_options_error(groups)

    def find_location(self, location: str) -> ResolvedLocation:
        """
        Find the MeteoSwiss point for a place name or a Swiss postal code.

        Case, accents and periods are ignored, and names in other languages such as "Genf" work
        too. A name that is not a MeteoSwiss point is looked up in the swisstopo places and
        answered for by the nearest MeteoSwiss point. Raise CannotAnswerError for an unknown
        location, and for a name of several places, listing them as options to ask again with.
        """
        self._ensure_chosen_points()
        normalised_location = _normalise_location(location)
        point = self._find_point_by_name(normalised_location)
        if point is not None:
            return ResolvedLocation(point, None)

        place = self._find_place(location)
        nearest_point = _find_nearest_point(place, self._points)
        return ResolvedLocation(nearest_point, place)

    def find_point(self, location: str) -> MeteoSwissPoint:
        """Find the MeteoSwiss point a location is answered for; see find_location."""
        return self.find_location(location).point
