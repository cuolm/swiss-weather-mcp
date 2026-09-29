"""
Find the location point for a place name or a Swiss postal code.

What MeteoSwiss publishes:
- A point table, ogd-local-forecasting_meta_point.csv, with the 5,614 places it
  publishes local forecasts for. MeteoSwiss names three types: 4,071 postal code
  centers, 912 weather stations and 631 points of interest, such as peaks, passes
  and mountain huts. Each has an ID, a name, a postal code (empty for stations and
  points of interest), an altitude and a position.
- Names and postal codes are not unique. For example, 24 points are called Zürich,
  and 12 share the postal code 1945.

    ogd-local-forecasting_meta_point.csv
    ┌───────────────────────────────────────────┐
    │ point_id;point_type_id;...;postal_code;   │  header
    │     point_name;...;point_height_masl;...  │
    │ 26;1;...;;Davos;...;1594.0;...            │  Davos station, no postal code
    │ 800100;2;...;8001;Zürich;...;409.0;...    │  Zürich 8001
    │ 800200;2;...;8002;Zürich;...;431.0;...    │  Zürich 8002, the same name
    │ ...                                       │  5,614 rows
    └───────────────────────────────────────────┘

The package also ships other_language_place_names.csv, built from Wikipedia: 349
names in Romansh, French, German, Italian and English, such as Genf, Ginevra and
Geneva for Genève.

How we use it:
1. On the first question, read the point table and keep it in memory for as long as
   the process runs. The file is downloaded first when it is missing or older than
   7 days.
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

    <cache dir>/locations/
    └── ogd-local-forecasting_meta_point.csv   (downloaded again when older than 7 days)
"""
import csv
import logging
import unicodedata
from datetime import timedelta
from importlib import resources
from pathlib import Path
from typing import Dict, List, NamedTuple, Tuple

from .errors import CannotAnswerError
from .opendata import ensure_recent_file

logger = logging.getLogger(__name__)

POINT_TABLE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-local-forecasting/ogd-local-forecasting_meta_point.csv"
POINT_TABLE_MAX_AGE = timedelta(days=7)
OTHER_LANGUAGE_PLACE_NAMES_FILE = "other_language_place_names.csv"


class LocationPoint(NamedTuple):
    """One of the places in the MeteoSwiss point table."""
    point_id: str
    point_type_id: str
    name: str
    postal_code: str
    altitude_m: float
    east_m: float   # LV95, the Swiss grid in metres
    north_m: float  # LV95

    @property
    def display_name(self) -> str:
        """The name shown to a reader, with postal code and altitude, such as "Zermatt 3920 (1610 m)"."""
        name_with_code = f"{self.name} {self.postal_code}" if self.postal_code else self.name
        return f"{name_with_code} ({self.altitude_m:.0f} m)"


def _rank_point(point: LocationPoint) -> Tuple[bool, str, int]:
    """Sort key for points that share a postal code or name; the first one is used."""
    return (not point.postal_code, point.postal_code, int(point.point_id))


def _normalise_location(location: str) -> str:
    """Fold case and strip accents, so "zurich" finds "Zürich"."""
    # NFKD splits "ü" into "u" and a separate accent mark, which is dropped here
    decomposed = unicodedata.normalize("NFKD", location.casefold())
    without_accents = ""
    for character in decomposed:
        if not unicodedata.combining(character):
            without_accents += character
    return without_accents.strip()


def _choose_point_per_postal_code(ranked_points: List[LocationPoint]) -> Dict[str, LocationPoint]:
    """Map each postal code to its first ranked point."""
    chosen_point_by_postal_code: Dict[str, LocationPoint] = {}
    for point in ranked_points:
        if not point.postal_code:
            continue  # stations and points of interest have none
        if point.postal_code not in chosen_point_by_postal_code:
            chosen_point_by_postal_code[point.postal_code] = point
    return chosen_point_by_postal_code


def _choose_point_per_name(ranked_points: List[LocationPoint]) -> Dict[str, LocationPoint]:
    """Map each normalised name to its first ranked point."""
    chosen_point_by_name: Dict[str, LocationPoint] = {}
    for point in ranked_points:
        point_name = _normalise_location(point.name)
        if point_name not in chosen_point_by_name:
            chosen_point_by_name[point_name] = point
    return chosen_point_by_name


def _load_other_language_place_names() -> Dict[str, str]:
    """Map each other language place name, normalised, to its normalised MeteoSwiss point name."""
    package_files = resources.files("swiss_weather_mcp")
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
    """Find location points by name or postal code in the MeteoSwiss point table, cached on disk."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "locations"
        self._point_count = 0
        self._chosen_point_by_postal_code: Dict[str, LocationPoint] = {}
        self._chosen_point_by_name: Dict[str, LocationPoint] = {}
        self._point_names_by_other_language_place_name = _load_other_language_place_names()

    def _read_point_table(self) -> List[LocationPoint]:
        """Read every point of the point table, downloading it when it is missing or old."""
        point_table_file = self.cache_dir / "ogd-local-forecasting_meta_point.csv"
        point_table = ensure_recent_file(POINT_TABLE_URL, point_table_file, POINT_TABLE_MAX_AGE)

        points = []
        with open(point_table, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                point = LocationPoint(
                    point_id=row["point_id"],
                    point_type_id=row["point_type_id"],
                    name=row["point_name"],
                    postal_code=row["postal_code"],
                    altitude_m=float(row["point_height_masl"]),
                    east_m=float(row["point_coordinates_lv95_east"]),
                    north_m=float(row["point_coordinates_lv95_north"]),
                )
                points.append(point)
        return points

    def _ensure_chosen_points(self) -> None:
        """Read the point table and choose one point per postal code and per name, once per process."""
        if self._chosen_point_by_postal_code and self._chosen_point_by_name:
            return

        points = self._read_point_table()
        ranked_points = sorted(points, key=_rank_point)
        self._point_count = len(points)
        self._chosen_point_by_postal_code = _choose_point_per_postal_code(ranked_points)
        self._chosen_point_by_name = _choose_point_per_name(ranked_points)
        logger.info(f"Loaded {len(points)} forecast locations")

    def find_point(self, location: str) -> LocationPoint:
        """
        Find the location point for a place name or a Swiss postal code.

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
