"""
Find the MeteoSwiss point for a place name or a Swiss postal code.

What it looks a location up in:
- The MeteoSwiss point table, see meteoswiss_points.py. Names and postal codes are not
  unique in it. For example, 24 points are called Zürich, and 12 share the postal code 1945.
- other_language_place_names.csv, which ships with the package and is built from
  Wikipedia: 349 names in Romansh, French, German, Italian and English, such as Genf,
  Ginevra and Geneva for Genève.

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
"""
import csv
import logging
import unicodedata
from importlib import resources
from pathlib import Path
from typing import Dict, List, Tuple

from ..errors import CannotAnswerError
from .meteoswiss_points import MeteoSwissPoint, MeteoSwissPointSource

logger = logging.getLogger(__name__)

OTHER_LANGUAGE_PLACE_NAMES_FILE = "other_language_place_names.csv"


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
    """Find MeteoSwiss points by name or postal code in the MeteoSwiss point table."""

    def __init__(self, cache_dir: Path):
        self.meteoswiss_point_source = MeteoSwissPointSource(cache_dir)
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
        self._point_count = len(points)
        self._chosen_point_by_postal_code = _choose_point_per_postal_code(ranked_points)
        self._chosen_point_by_name = _choose_point_per_name(ranked_points)
        logger.info(f"Loaded {len(points)} forecast locations")

    def find_point(self, location: str) -> MeteoSwissPoint:
        """
        Find the MeteoSwiss point for a place name or a Swiss postal code.

        Case and accents are ignored, and names in other languages such as "Genf" work too.
        Raise CannotAnswerError when MeteoSwiss has no forecast for the place.
        """
        self._ensure_chosen_points()
        normalised_location = _normalise_location(location)

        if normalised_location in self._chosen_point_by_postal_code:
            return self._chosen_point_by_postal_code[normalised_location]

        if normalised_location in self._chosen_point_by_name:
            return self._chosen_point_by_name[normalised_location]

        if normalised_location in self._point_names_by_other_language_place_name:
            meteoswiss_name = self._point_names_by_other_language_place_name[normalised_location]
            # The names file was built from an older point table, so its place may be gone
            if meteoswiss_name in self._chosen_point_by_name:
                return self._chosen_point_by_name[meteoswiss_name]

        raise CannotAnswerError(
            f"Location '{location}' is not one of the {self._point_count} places MeteoSwiss publishes "
            f"forecasts for. Check the spelling, or try a nearby town, village or postal code."
        )
