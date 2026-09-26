"""
Read the latest measurements of the SwissMetNet weather stations from MeteoSwiss Open Data.

MeteoSwiss publishes the newest 10-minute values of all its automatic stations in one small file,
updated about every 10 minutes, and where each station is in a separate station table.
"""
import csv
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from . import parameters
from ..locations import ForecastPoint
from ..opendata import download_file, parse_stamp

logger = logging.getLogger(__name__)

STATION_TABLE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/ogd-smn_meta_stations.csv"
CURRENT_VALUES_URL = "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-aktuell/VQHA80.csv"

STATION_TABLE_MAX_AGE = timedelta(days=7)
# MeteoSwiss replaces the current values about every 10 minutes
CURRENT_VALUES_MAX_AGE = timedelta(minutes=5)

# The current values file writes a value a station does not have as "-"
MISSING_VALUE_MARKERS = {"", "-"}


class Station(NamedTuple):
    """A SwissMetNet weather station as the MeteoSwiss station table describes it."""
    abbr: str
    name: str
    altitude_m: float
    east_m: float   # LV95, the Swiss grid in metres
    north_m: float  # LV95

    @property
    def display_name(self) -> str:
        """The name shown to a reader, with the altitude, such as "Zürich / Fluntern (604 m)"."""
        return f"{self.name} ({self.altitude_m:.0f} m)"


class StationMeasurements(NamedTuple):
    """The latest measurements of one station, keyed by parameter; None where it has no value."""
    station: Station
    measured_at: datetime
    values: Dict[str, Optional[float]]


def find_distance_m(point: ForecastPoint, station: Station) -> float:
    """Return the distance in metres between a forecast point and a station."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(station.east_m - point.east_m, station.north_m - point.north_m)


def _parse_value(value_text: str) -> Optional[float]:
    """Read one measured value, or None when the station has none."""
    if value_text.strip() in MISSING_VALUE_MARKERS:
        return None
    return float(value_text)


def _ensure_recent_file(file_url: str, target_file: Path, max_age: timedelta) -> Path:
    """Return a cached file, downloading it again when it is missing or older than max_age."""
    if target_file.exists():
        modified_at = datetime.fromtimestamp(target_file.stat().st_mtime, timezone.utc)
        if datetime.now(timezone.utc) - modified_at < max_age:
            return target_file

    logger.info(f"Downloading {file_url}")
    download_file(file_url, target_file)
    return target_file


class MeasurementSource:
    """Read the latest SwissMetNet measurements and find the station nearest to a forecast point."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "measurements"
        self._stations: Dict[str, Station] = {}

    def _load_stations(self) -> Dict[str, Station]:
        """Read the station table into memory once per process, keyed by station abbreviation."""
        if self._stations:
            return self._stations

        station_table = _ensure_recent_file(
            STATION_TABLE_URL, self.cache_dir / "ogd-smn_meta_stations.csv", STATION_TABLE_MAX_AGE
        )
        stations: Dict[str, Station] = {}
        with open(station_table, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                stations[row["station_abbr"]] = Station(
                    abbr=row["station_abbr"],
                    name=row["station_name"],
                    altitude_m=float(row["station_height_masl"]),
                    east_m=float(row["station_coordinates_lv95_east"]),
                    north_m=float(row["station_coordinates_lv95_north"]),
                )
        # Published only once complete, so a parallel request never sees part of the table
        self._stations = stations
        logger.info(f"Loaded {len(stations)} weather stations")
        return stations

    def read_current_measurements(self) -> List[StationMeasurements]:
        """
        Read the latest measurements of every station that is in the station table.

        Returns:
            List[StationMeasurements]: One entry per station, with its values keyed by parameter.
        """
        stations = self._load_stations()
        current_values = _ensure_recent_file(
            CURRENT_VALUES_URL, self.cache_dir / "VQHA80.csv", CURRENT_VALUES_MAX_AGE
        )

        all_measurements = []
        with open(current_values, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                station = stations.get(row["Station/Location"])
                if station is None:
                    continue  # a station without a place in the station table cannot be located
                values = {parameter: _parse_value(row[parameter]) for parameter in parameters.ALL_PARAMETERS}
                all_measurements.append(StationMeasurements(station, parse_stamp(row["Date"]), values))
        return all_measurements

    def find_nearest_measurements(self, point: ForecastPoint) -> StationMeasurements:
        """
        Find the latest measurements of the station nearest to a forecast point that measures the
        temperature.

        Parameters:
            point (ForecastPoint): The resolved forecast point.

        Returns:
            StationMeasurements: The station, the time of its measurements and its values.
        """
        with_temperature = []
        for measurements in self.read_current_measurements():
            if measurements.values[parameters.TEMPERATURE] is not None:
                with_temperature.append(measurements)
        if not with_temperature:
            raise ValueError("MeteoSwiss currently publishes no temperature measurements, try again later.")

        def distance_to_point(measurements: StationMeasurements) -> float:
            return find_distance_m(point, measurements.station)

        return min(with_temperature, key=distance_to_point)
