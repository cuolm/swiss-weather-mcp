"""
Read point forecasts from the MeteoSwiss local forecasting collection.

Terms:
    STAC item   One JSON document per UTC day, such as 20260929-ch, that lists the
                files published that day. STAC (SpatioTemporal Asset Catalog) is a
                standard format for catalogs of geodata files.
    asset       One file in an item. Its "href" is the file's download URL.
    run         One hourly computation of the forecast. run_id is its start time in
                UTC as text, such as "202609290900", and names its files and cache
                folder; run_time is the same moment as a datetime.
    parameter   The weather value a file holds, such as tre200h0 (temperature).
    point       A place with a forecast, identified by point_id and point_type_id
                together.

What MeteoSwiss publishes:
- A point table, ogd-local-forecasting_meta_point.csv, with the 5,614 points it
  forecasts for: 4,071 postal code centers, 912 weather stations and 631 points of
  interest, such as peaks, passes and mountain huts. Each has an ID, a name, an
  altitude and a position in LV95, the Swiss grid in metres.
- Each hourly run adds one CSV file per parameter to the item of its UTC day.
- One file holds the values of one parameter for all 5,614 points and every time
  step: hourly, or one per day for daily values.

    collection ch.meteoschweiz.ogd-local-forecasting
    └── item 20260928-ch                           (one per UTC day)
        ├── vnut12.lssw.202609280000.tre200h0.csv  (run 00:00 UTC, temperature)
        ├── vnut12.lssw.202609280000.rre003i0.csv  (run 00:00 UTC, rainfall)
        ├── ...                                    (32 parameters per run)
        └── vnut12.lssw.202609280900.tre200h0.csv  (run 09:00 UTC, temperature)

    vnut12.lssw.202609280900.tre200h0.csv
    ┌──────────────────────────────────────────┐
    │ point_id;point_type_id;Date;tre200h0     │  header: the last column is the parameter
    │ 1;1;202609272100;12.0                    │  Arosa station, 21:00 UTC, 12.0 °C
    │ ...                                      │
    │ 800100;2;202609272100;18.5               │  Zürich 8001, 21:00 UTC, 18.5 °C
    │ 800100;2;202609272200;17.8               │  Zürich 8001, 22:00 UTC, 17.8 °C
    │ ...                                      │  about 1.2 million rows
    └──────────────────────────────────────────┘

How we use it:
1. Choose the point to forecast a place from. Weather changes more with height than
   with distance, so among the points within 5 km take the one with the smallest
   distance plus 20 times the height gap: 100 m higher or lower counts like 2 km
   farther away. With no point within 5 km, take the nearest one. The point table is
   read on the first question and kept in memory; it is downloaded first when it is
   missing or older than 7 days.

       Oeschinensee, a lake at 1575 m
         Fründenhütte SAC  2.0 km, 987 m higher   2.0 + 19.7 = 21.7 km
         Oeschinensee      2.3 km, 106 m higher   2.3 +  2.1 =  4.4 km   chosen

2. Read today's STAC item (yesterday's just after midnight UTC) and pick the newest run
   that has all parameters we need. MeteoSwiss uploads a run file by file, so the
   newest run can be incomplete.
3. Download a parameter's file only when a tool asks for it. By default, keep only the
   rows of the requested point; with cache_all_locations, keep the whole file.
4. Cache the files per run, and delete older runs except the one just before.

    <cache dir>/forecast/
    ├── ogd-local-forecasting_meta_point.csv   (downloaded again when older than 7 days)
    └── runs/
        ├── 202609280800/                    (previous run, kept for requests still reading it)
        └── 202609280900/                    (current run)
            ├── tre200h0_800100_2.csv        (temperature, Zürich 8001 rows only)
            ├── sre000h0_800100_2.csv        (sunshine, Zürich 8001 rows only)
            └── ...                          (one file per parameter and point asked for)

    With cache_all_locations, a run folder holds whole files instead:
        └── 202609280900/
            ├── tre200h0.csv                 (temperature, all points)
            └── ...                          (one file per parameter asked for)
"""
import csv
import logging
import math
import shutil
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

import requests

from . import parameters
from ..errors import CannotAnswerError
from ..opendata import REQUEST_TIMEOUT_SECONDS, download_file, ensure_recent_file, parse_stamp
from ..places import SwisstopoPlace

logger = logging.getLogger(__name__)

COLLECTION_ID = "ch.meteoschweiz.ogd-local-forecasting"
STAC_BASE_URL = "https://data.geo.admin.ch/api/stac/v1"
RUN_LOOKUP_MAX_AGE = timedelta(minutes=5)

POINT_TABLE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-local-forecasting/ogd-local-forecasting_meta_point.csv"
POINT_TABLE_MAX_AGE = timedelta(days=7)
# Only a point this close to a place is chosen for its height; farther, the nearest point is taken
FORECAST_POINT_RADIUS_M = 5000
# Weather changes more with height than with distance: 1 m of height gap counts like 20 m farther away
METRES_PER_HEIGHT_METRE = 20


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


class ForecastSeries(NamedTuple):
    """One parameter over the whole forecast window at one point, with the run it came from."""
    run_time: datetime
    values: Dict[datetime, float]


def calculate_distance_m(place: SwisstopoPlace, point: MeteoSwissPoint) -> float:
    """Return the distance in metres between a place and a forecast point."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(place.east_m - point.east_m, place.north_m - point.north_m)


def _build_row_prefix(point: MeteoSwissPoint) -> bytes:
    """Return the start every row of a point has in a parameter file, such as b"800100;2;"."""
    return f"{point.point_id};{point.point_type_id};".encode()


class ForecastSource:
    """Read point forecasts from the MeteoSwiss local forecasting collection, cached per model run."""

    def __init__(self, cache_dir: Path, cache_all_locations: bool = False):
        self.cache_dir = cache_dir / "forecast"
        self.cache_all_locations = cache_all_locations
        self._run_id: Optional[str] = None
        self._run_file_urls: Dict[str, str] = {}
        self._run_checked_at: Optional[datetime] = None
        # A tool reads its files in parallel, and all of them must come from the same run
        self._run_lookup_lock = threading.Lock()
        self._points: List[MeteoSwissPoint] = []

    def _load_points(self) -> List[MeteoSwissPoint]:
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
        logger.info(f"Loaded {len(points)} forecast points")
        return points

    def find_forecast_point(self, place: SwisstopoPlace) -> MeteoSwissPoint:
        """
        Find the point to forecast a place from: among the points within FORECAST_POINT_RADIUS_M,
        the one with the smallest distance plus METRES_PER_HEIGHT_METRE times the height gap,
        else the nearest point.
        """
        points = self._load_points()
        close_points = [point for point in points if calculate_distance_m(place, point) <= FORECAST_POINT_RADIUS_M]
        if not close_points:
            return min(points, key=lambda point: calculate_distance_m(place, point))

        def weigh_point(point: MeteoSwissPoint) -> float:
            height_gap_m = abs(point.altitude_m - place.altitude_m)
            return calculate_distance_m(place, point) + METRES_PER_HEIGHT_METRE * height_gap_m

        return min(close_points, key=weigh_point)

    def _fetch_csv_file_urls_by_run(self, day: date) -> Dict[str, Dict[str, str]]:
        """
        Fetch the STAC item of one UTC day and group its CSV file URLs by run and parameter.

        In, the assets of item 20260929-ch, 12 runs of 32 files each:

            "vnut12.lssw.202609290000.tre200h0.csv": {"href": "https://...", ...}
            "vnut12.lssw.202609290000.dkl010h0.csv": {"href": "https://...", ...}
            "vnut12.lssw.202609290100.tre200h0.csv": {"href": "https://...", ...}
            ...

        Out, one dict per run, with the URL of each parameter's file:

            {
                "202609290000": {"tre200h0": "https://...", "dkl010h0": "https://...", ...},
                "202609290100": {"tre200h0": "https://...", ...},
                ...
            }

        Return an empty dict when MeteoSwiss has not created the day's item yet.
        """
        item_url = f"{STAC_BASE_URL}/collections/{COLLECTION_ID}/items/{day.strftime('%Y%m%d')}-ch"
        response = requests.get(item_url, timeout=REQUEST_TIMEOUT_SECONDS)
        if response.status_code == 404:
            return {}
        response.raise_for_status()

        item = response.json()
        assets = item.get("assets", {})
        csv_file_urls_by_run: Dict[str, Dict[str, str]] = {}
        for asset_name, asset in assets.items():
            # Asset names look like vnut12.lssw.<run>.<parameter>.csv
            name_parts = asset_name.split(".")
            if len(name_parts) != 5:
                continue
            _, _, run_id, parameter, _ = name_parts
            if run_id not in csv_file_urls_by_run:
                csv_file_urls_by_run[run_id] = {}
            csv_file_urls_by_run[run_id][parameter] = asset["href"]
        return csv_file_urls_by_run

    def _find_latest_run(self) -> Tuple[str, Dict[str, str]]:
        """
        Find the newest published run and the parameter files it holds, reusing the answer for
        RUN_LOOKUP_MAX_AGE.

        Returns:
            Tuple[str, Dict[str, str]]: The run ID (YYYYMMDDHHMM, UTC) and its parameter file URLs.
        """
        with self._run_lookup_lock:
            now = datetime.now(timezone.utc)
            if self._run_id is not None and self._run_checked_at and now - self._run_checked_at < RUN_LOOKUP_MAX_AGE:
                return self._run_id, self._run_file_urls

            # Just after 00:00 UTC, today's item may hold no complete run yet, so look in yesterday's too
            today = now.date()
            for day in (today, today - timedelta(days=1)):
                csv_file_urls_by_run = self._fetch_csv_file_urls_by_run(day)
                complete_run_ids = []
                for run_id, file_urls in csv_file_urls_by_run.items():
                    if parameters.ALL_PARAMETERS.issubset(file_urls):
                        complete_run_ids.append(run_id)
                if complete_run_ids:
                    # Run IDs are fixed width, so the newest run is the largest string
                    self._run_id = max(complete_run_ids)
                    self._run_file_urls = csv_file_urls_by_run[self._run_id]
                    self._run_checked_at = now
                    return self._run_id, self._run_file_urls

        raise RuntimeError(
            "The MeteoSwiss local forecasting collection published no complete run for today or yesterday"
        )

    def _delete_old_cached_runs(self, current_run_id: str) -> None:
        """Delete the cached runs older than the current one, except the run just before it."""
        # Run IDs are fixed width, so comparing them as text compares them in time
        older_runs = []
        for folder in (self.cache_dir / "runs").iterdir():
            if folder.is_dir() and folder.name < current_run_id:
                older_runs.append(folder)

        older_runs.sort()

        # Keep the previous run. Another request, or another server using the same cache, may
        # still read from it for a few minutes. Runs come an hour apart, so one is enough.
        for folder in older_runs[:-1]:
            logger.info(f"Deleting old cached run {folder.name}")
            shutil.rmtree(folder, ignore_errors=True)

    def _ensure_parameter_file(self, parameter: str, point: MeteoSwissPoint, run_id: str, file_url: str) -> Path:
        """Return the cached file with this parameter for this point and run, downloading it if missing."""
        run_dir = self.cache_dir / "runs" / run_id
        full_file = run_dir / f"{parameter}.csv"
        point_file = run_dir / f"{parameter}_{point.point_id}_{point.point_type_id}.csv"

        # A full file from an earlier download serves every point
        if full_file.exists():
            return full_file
        if point_file.exists():
            return point_file

        logger.info(f"Reading {parameter} for {point.display_name} from run {run_id}")
        if self.cache_all_locations:
            download_file(file_url, full_file)
            downloaded_file = full_file
        else:
            row_prefix = _build_row_prefix(point)
            download_file(file_url, point_file, only_rows_starting_with=row_prefix)
            downloaded_file = point_file

        self._delete_old_cached_runs(run_id)
        return downloaded_file

    def _read_point_values(self, parameter_file: Path, point: MeteoSwissPoint) -> Dict[datetime, float]:
        """Read one point's values from a cached file, keyed by UTC timestamp."""
        row_prefix = _build_row_prefix(point)
        values: Dict[datetime, float] = {}

        with open(parameter_file, "rb") as file:
            for line in file:
                # This also skips the header of a full file, a point extract has none
                if not line.startswith(row_prefix):
                    continue
                _, _, stamp_text, value_text = line.decode("latin-1").strip().split(";")
                try:
                    stamp = parse_stamp(stamp_text)
                    values[stamp] = float(value_text)
                except ValueError:
                    continue  # gaps are published as empty fields

        return values

    def read_series(self, parameter: str, point: MeteoSwissPoint) -> ForecastSeries:
        """
        Read one parameter over the whole forecast window at one point.

        Raise CannotAnswerError when the newest run has no values for the parameter at this point.

        Parameters:
            parameter (str): MeteoSwiss parameter shortname (e.g., "tre200h0", "fu3010h0").
            point (MeteoSwissPoint): The resolved MeteoSwiss point.

        Returns:
            ForecastSeries: The run the values came from, and the values keyed by UTC timestamp.
        """
        run_id, file_urls = self._find_latest_run()
        if parameter not in file_urls:
            raise CannotAnswerError(f"MeteoSwiss's newest forecast does not include '{parameter}'.")

        parameter_file = self._ensure_parameter_file(parameter, point, run_id, file_urls[parameter])
        values = self._read_point_values(parameter_file, point)
        if not values:
            raise CannotAnswerError(
                f"MeteoSwiss publishes no '{parameter}' values for {point.display_name}. Some entries, "
                f"such as the regional ones, only carry part of the forecast, try a nearby town."
            )

        return ForecastSeries(run_time=parse_stamp(run_id), values=values)
