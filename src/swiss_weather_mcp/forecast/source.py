"""
Read point forecasts from the MeteoSwiss local forecasting collection.

What MeteoSwiss publishes:
- One STAC item per UTC day, such as 20260928-ch. STAC (SpatioTemporal Asset Catalog)
  is a standard JSON format that lists geodata files. An item is one entry in the
  catalog, and its "assets" are the files it lists, each with a download URL.
- Each hourly model run adds one CSV file per forecast parameter to that day's item,
  such as the temperature or the rainfall.
- One file holds the forecast values of one parameter, for all 5,614 points and every
  time step: hourly, or one per day for daily values. A point is identified by point_id
  and point_type_id together; the type is 1 for a weather station, 2 for a postal code
  area and 3 for a point of interest.

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
1. Read today's STAC item (yesterday's just after midnight UTC) and pick the newest run
   that has all parameters we need. MeteoSwiss uploads a run file by file, so the
   newest run can be incomplete.
2. Download a parameter's file only when a tool asks for it. By default, keep only the
   rows of the requested point; with cache_all_locations, keep the whole file.
3. Cache the files per run, and delete older runs except the one just before.

    <cache dir>/forecast/runs/
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
import logging
import shutil
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, NamedTuple, Optional, Tuple

import requests

from . import parameters
from ..errors import CannotAnswerError
from ..locations import LocationPoint
from ..opendata import REQUEST_TIMEOUT_SECONDS, download_file, parse_stamp

logger = logging.getLogger(__name__)

COLLECTION_ID = "ch.meteoschweiz.ogd-local-forecasting"
STAC_BASE_URL = "https://data.geo.admin.ch/api/stac/v1"
RUN_LOOKUP_MAX_AGE = timedelta(minutes=5)


class ForecastSeries(NamedTuple):
    """One parameter over the whole forecast window at one point, with the run it came from."""
    run_time: datetime
    values: Dict[datetime, float]


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

    def _fetch_csv_file_urls_by_run(self, day: date) -> Dict[str, Dict[str, str]]:
        """
        Return the file URLs of one daily STAC item: for each run, the URL of each parameter's file,
        such as {"202609280900": {"tre200h0": "https://..."}}.
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

    def _ensure_parameter_file(self, parameter: str, point: LocationPoint, run_id: str, file_url: str) -> Path:
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
            download_file(file_url, point_file, only_rows_starting_with=point.row_prefix)
            downloaded_file = point_file

        self._delete_old_cached_runs(run_id)
        return downloaded_file

    def _read_point_values(self, parameter_file: Path, point: LocationPoint) -> Dict[datetime, float]:
        """Read one point's values from a cached file, keyed by UTC timestamp."""
        row_prefix = point.row_prefix
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

    def read_series(self, parameter: str, point: LocationPoint) -> ForecastSeries:
        """
        Read one parameter over the whole forecast window at one point.

        Parameters:
            parameter (str): MeteoSwiss parameter shortname (e.g., "tre200h0", "fu3010h0").
            point (LocationPoint): The resolved location point.

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
