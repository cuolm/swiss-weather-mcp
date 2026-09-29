"""
Read the latest measurements of the SwissMetNet weather stations from MeteoSwiss Open Data.

What MeteoSwiss publishes:
- A current values file, VQHA80.csv, with one row per automatic weather station and
  about 160 stations, all with the same timestamp. Some values are measured at that
  time, such as the temperature; others cover the 10 minutes up to it, such as the
  rainfall and the sunshine. MeteoSwiss replaces the file about every 10 minutes.
  A value a station does not measure is "-".
- A station table, ogd-smn_meta_stations.csv, with one row per station: its name,
  altitude and position in LV95, the Swiss grid in metres.
- Both files use ";" between columns. A station abbreviation, such as SMA, links a row
  in one file to the row in the other.

    VQHA80.csv  (current values, 22 columns, the server reads 9 parameters)
    ┌──────────────────────────────────────────────────────┐
    │ Station/Location;Date;tre200s0;rre150z0;sre000z0;... │  station, time, temperature, rainfall, sunshine
    │ ARO;202609281020;19.10;-;10.00;...                   │  Arosa: 19.1 °C, no rainfall value, 10 min of sun
    │ SMA;202609281020;21.80;0.00;10.00;...                │  Zürich / Fluntern: 21.8 °C, 0.0 mm, 10 min of sun
    │ ...                                                  │  about 160 rows
    └──────────────────────────────────────────────────────┘

    ogd-smn_meta_stations.csv  (station table)
    ┌────────────────────────────────────────────────────────────────┐
    │ station_abbr;station_name;...;station_height_masl;...          │  header
    │ ARO;Arosa;...;1878.0;...;2771031.0;1184830.0;...               │  altitude, LV95 east, north
    │ SMA;Zürich / Fluntern;...;604.0;...;2685223.0;1248410.0;...    │
    │ ...                                                            │  about 160 rows
    └────────────────────────────────────────────────────────────────┘

How we use it:
1. Read the current values on every question; the file is downloaded again when it is
   older than 5 minutes. Read the station table on the first question and keep it in
   memory for as long as the process runs; the file is downloaded first when it is
   missing or older than 7 days.
2. Join each current values row to its station by abbreviation. A station missing from
   the station table has no position, so it is left out.
3. Find the station nearest to the requested location point that measures the
   temperature. Stations without it, such as a wind tower, would answer with almost
   every value missing.

    <cache dir>/measurements/
    ├── VQHA80.csv                    (current values, downloaded again after 5 minutes)
    └── ogd-smn_meta_stations.csv     (station table, downloaded again when older than 7 days)
"""
import csv
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

from . import parameters
from ..errors import CannotAnswerError
from ..locations import LocationPoint
from ..opendata import ensure_recent_file, parse_stamp

logger = logging.getLogger(__name__)

STATION_TABLE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/ogd-smn_meta_stations.csv"
CURRENT_VALUES_URL = "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-aktuell/VQHA80.csv"

STATION_TABLE_MAX_AGE = timedelta(days=7)
# Shorter than MeteoSwiss's 10-minute update, so a new file is used at most 5 minutes after it appears
CURRENT_VALUES_MAX_AGE = timedelta(minutes=5)

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


def calculate_distance_m(point: LocationPoint, station: Station) -> float:
    """Return the distance in metres between a location point and a station."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(station.east_m - point.east_m, station.north_m - point.north_m)


def _parse_value(value_text: str) -> Optional[float]:
    """Read one measured value, or None when the station has none."""
    if value_text.strip() in MISSING_VALUE_MARKERS:
        return None
    return float(value_text)


class MeasurementSource:
    """Read the latest SwissMetNet measurements and find the station nearest to a location point."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "measurements"
        self._stations: Dict[str, Station] = {}

    def _load_stations(self) -> Dict[str, Station]:
        """Read the station table into memory once per process, keyed by station abbreviation."""
        if self._stations:
            return self._stations

        station_table_file = self.cache_dir / "ogd-smn_meta_stations.csv"
        station_table = ensure_recent_file(STATION_TABLE_URL, station_table_file, STATION_TABLE_MAX_AGE)
        stations: Dict[str, Station] = {}
        with open(station_table, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                station = Station(
                    abbr=row["station_abbr"],
                    name=row["station_name"],
                    altitude_m=float(row["station_height_masl"]),
                    east_m=float(row["station_coordinates_lv95_east"]),
                    north_m=float(row["station_coordinates_lv95_north"]),
                )
                stations[station.abbr] = station
        # Published only once complete, so a parallel request never sees part of the table
        self._stations = stations
        logger.info(f"Loaded {len(stations)} weather stations")
        return stations

    def _read_current_measurements(self) -> List[StationMeasurements]:
        """Read the latest measurements of every station in the station table, None where a station has no value."""
        stations = self._load_stations()
        current_values_file = self.cache_dir / "VQHA80.csv"
        current_values = ensure_recent_file(CURRENT_VALUES_URL, current_values_file, CURRENT_VALUES_MAX_AGE)

        all_measurements = []
        with open(current_values, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                station = stations.get(row["Station/Location"])
                if station is None:
                    continue  # a station without a place in the station table cannot be located
                measured_at = parse_stamp(row["Date"])
                values: Dict[str, Optional[float]] = {}
                for parameter in parameters.ALL_PARAMETERS:
                    values[parameter] = _parse_value(row[parameter])
                measurements = StationMeasurements(station, measured_at, values)
                all_measurements.append(measurements)
        return all_measurements

    def find_nearest_measurements(self, point: LocationPoint) -> StationMeasurements:
        """
        Find the latest measurements of the station nearest to a location point that measures the
        temperature.

        Raise CannotAnswerError when no station currently publishes a temperature.
        """
        nearest_measurements: Optional[StationMeasurements] = None
        nearest_distance_m = math.inf
        current_measurements = self._read_current_measurements()
        for measurements in current_measurements:
            # Some stations measure only a few values, such as wind on a tower, and would answer
            # with almost every value None; a temperature marks a station that measures the usual set
            if measurements.values[parameters.TEMPERATURE] is None:
                continue
            distance_m = calculate_distance_m(point, measurements.station)
            if distance_m < nearest_distance_m:
                nearest_measurements = measurements
                nearest_distance_m = distance_m

        if nearest_measurements is None:
            raise CannotAnswerError("MeteoSwiss currently publishes no temperature measurements, try again later.")
        return nearest_measurements
