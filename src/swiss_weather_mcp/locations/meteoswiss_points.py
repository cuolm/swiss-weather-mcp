"""
Read the places MeteoSwiss publishes a local forecast for.

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

How we use it:
Read every point of the table on the first question and keep the points in memory for
as long as the process runs. The file is downloaded first when it is missing or older
than 7 days.

    <cache dir>/locations/
    └── ogd-local-forecasting_meta_point.csv   (downloaded again when older than 7 days)
"""
import csv
from datetime import timedelta
from pathlib import Path
from typing import List, NamedTuple

from ..opendata import ensure_recent_file

POINT_TABLE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-local-forecasting/ogd-local-forecasting_meta_point.csv"
POINT_TABLE_MAX_AGE = timedelta(days=7)


class MeteoSwissPoint(NamedTuple):
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


class MeteoSwissPointSource:
    """Read the MeteoSwiss point table, cached on disk."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "locations"
        self._points: List[MeteoSwissPoint] = []

    def load_points(self) -> List[MeteoSwissPoint]:
        """Read the point table into memory once per process, downloading it when it is missing or old."""
        if self._points:
            return self._points

        point_table_file = self.cache_dir / "ogd-local-forecasting_meta_point.csv"
        point_table = ensure_recent_file(POINT_TABLE_URL, point_table_file, POINT_TABLE_MAX_AGE)

        points = []
        with open(point_table, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                point = MeteoSwissPoint(
                    point_id=row["point_id"],
                    point_type_id=row["point_type_id"],
                    name=row["point_name"],
                    postal_code=row["postal_code"],
                    altitude_m=float(row["point_height_masl"]),
                    east_m=float(row["point_coordinates_lv95_east"]),
                    north_m=float(row["point_coordinates_lv95_north"]),
                )
                points.append(point)
        # Published only once complete, so a parallel request never sees part of the points
        self._points = points
        return points
