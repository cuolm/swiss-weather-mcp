"""
Read the latest measurements of the SwissMetNet weather stations from MeteoSwiss Open Data.

What MeteoSwiss publishes:
- A now file per station, such as ogd-smn_sma_t_now.csv, with one row every 10 minutes
  since 00:00 UTC today. Some values are measured at a row's time, such as the
  temperature; others cover the 10 minutes up to it, such as the rainfall and the
  sunshine. MeteoSwiss adds a row about every 10 minutes. A value a station does not
  measure is empty.
- An all stations file, VQHA80.csv, with the latest row of every station, about 160
  rows, all with the same timestamp. It holds no earlier rows. A value a station does
  not measure, or has not delivered yet, is "-".
- Two daily files per station, with one row per UTC day: ogd-smn_sma_d_recent.csv from
  1 January of this year until yesterday, and ogd-smn_sma_d_historical.csv from the start
  of the station until 31 December of last year. MeteoSwiss adds yesterday's row in the
  night, and updates the historical file once a year.
- A station table, ogd-smn_meta_stations.csv, with one row per station: its name,
  the abbreviation of its canton, the day its measurements start, altitude and position
  in LV95, the Swiss grid in metres.
- All files use ";" between columns. A station abbreviation, such as SMA, links the
  station table to the station's now file and to its row in the all stations file.

    ogd-smn_sma_t_now.csv  (now file of Zürich / Fluntern, 34 columns, the server reads 9 parameters)
    ┌───────────────────────────────────────────────────────────┐
    │ station_abbr;reference_timestamp;tre200s0;...;rre150z0;... │  station, time, temperature, ..., rainfall
    │ SMA;30.09.2026 00:00;17;...;0;...                         │  17.0 °C, 0.0 mm
    │ SMA;30.09.2026 00:10;16.8;...;0;...                       │
    │ ...                                                       │  one row every 10 minutes
    └───────────────────────────────────────────────────────────┘

    VQHA80.csv  (all stations, 22 columns, the server reads 9 parameters)
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

How we use it for the weather at one place:
1. Read the station table on the first question and keep it in memory for as long as
   the process runs; the file is downloaded first when it is missing or older than 7 days.
2. Rank the stations by their distance to the requested place plus METRES_PER_HEIGHT_METRE
   times their height gap, as the weather changes faster with height than with distance.
3. Read the now file of the first station; the file is downloaded again when it is
   older than 5 minutes. Its last row holds the latest measurements, and its earlier rows
   show how the values changed.
4. Take the next station when the latest measurements have no temperature or are older
   than 1 hour, at most 3 stations. Stations without a temperature, such as a wind tower,
   would answer with almost every value missing, and a station that stopped publishing
   would answer with old values.

How we use it for past days at one place:
1. Refuse today and later days, which have no daily row yet.
2. Rank the stations as for the weather now, and read the daily files of the first
   station: the recent file for days of this year, downloaded again when older than
   1 hour, and the historical file for earlier days, downloaded again when older than
   30 days.
3. Take the next station when the station started after the days, as the station
   table says, or has no temperature on any of the days, at most 3 stations. Stations
   started in different years, and some measured the temperature only years later.

How we use it to compare all stations:
1. Read the all stations file; it is downloaded again when it is older than 5 minutes.
2. Join each row to its station by abbreviation. A station missing from the station
   table has no name and no canton, so it is left out.

    <cache dir>/measurements/
    ├── ogd-smn_meta_stations.csv      (station table, downloaded again when older than 7 days)
    ├── ogd-smn_sma_t_now.csv          (now file per station, downloaded again after 5 minutes)
    ├── ogd-smn_sma_d_recent.csv       (days of this year per station, downloaded again after 1 hour)
    ├── ogd-smn_sma_d_historical.csv   (earlier days per station, downloaded again after 30 days)
    └── VQHA80.csv                     (all stations, downloaded again after 5 minutes)
"""
import csv
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence

from . import parameters
from ..distance import calculate_weighted_distance_m
from ..errors import CannotAnswerError
from ..downloads import ensure_recent_file
from ..places import SwisstopoPlace

logger = logging.getLogger(__name__)

STATION_TABLE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/ogd-smn_meta_stations.csv"
STATION_NOW_VALUES_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/{abbr}/ogd-smn_{abbr}_t_now.csv"
# The period is RECENT_DAYS or HISTORICAL_DAYS
STATION_DAYS_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/{abbr}/ogd-smn_{abbr}_d_{period}.csv"
ALL_STATIONS_LATEST_VALUES_URL = "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-aktuell/VQHA80.csv"
RECENT_DAYS = "recent"
HISTORICAL_DAYS = "historical"

STATION_TABLE_MAX_AGE = timedelta(days=7)
# Shorter than MeteoSwiss's 10-minute update, so a new file is used at most 5 minutes after it appears
STATION_NOW_VALUES_MAX_AGE = timedelta(minutes=5)
ALL_STATIONS_LATEST_VALUES_MAX_AGE = timedelta(minutes=5)
# MeteoSwiss updates the recent daily files twice a day, and the historical ones once a year
PERIOD_TO_MAX_AGE = {
    RECENT_DAYS: timedelta(hours=1),
    HISTORICAL_DAYS: timedelta(days=30),
}
# Older latest measurements mean the station stopped publishing
LATEST_MEASUREMENTS_MAX_AGE = timedelta(hours=1)
MAX_STATIONS_TRIED = 3

MISSING_VALUE_MARKERS = {"", "-"}

# The station table names a canton by its abbreviation, and lists Liechtenstein like a canton
CANTON_ABBREVIATION_TO_NAME = {
    "AG": "Aargau", "AI": "Appenzell Innerrhoden", "AR": "Appenzell Ausserrhoden", "BE": "Bern",
    "BL": "Basel-Landschaft", "BS": "Basel-Stadt", "FR": "Fribourg", "GE": "Geneva", "GL": "Glarus",
    "GR": "Graubünden", "JU": "Jura", "LU": "Lucerne", "NE": "Neuchâtel", "NW": "Nidwalden",
    "OW": "Obwalden", "SG": "St. Gallen", "SH": "Schaffhausen", "SO": "Solothurn", "SZ": "Schwyz",
    "TG": "Thurgau", "TI": "Ticino", "UR": "Uri", "VD": "Vaud", "VS": "Valais", "ZG": "Zug",
    "ZH": "Zurich", "FL": "Liechtenstein",
}


class MeteoSwissStation(NamedTuple):
    """A SwissMetNet weather station as the MeteoSwiss station table describes it."""
    abbr: str
    name: str
    canton: str  # the full name, such as "Zurich"
    altitude_m: float
    east_m: float   # LV95, the Swiss grid in metres
    north_m: float  # LV95
    data_since: date  # the first day of any of its measurements; its temperature may start years later

    @property
    def display_name(self) -> str:
        """The name shown to a reader, with the altitude, such as "Zürich / Fluntern (604 m)"."""
        return f"{self.name} ({self.altitude_m:.0f} m)"


class MeteoSwissStationMeasurements(NamedTuple):
    """The measurements of one station at one time, keyed by parameter; None where it has no value."""
    station: MeteoSwissStation
    measured_at: datetime
    parameter_to_value: Dict[str, Optional[float]]


def _parse_value(value_text: str) -> Optional[float]:
    """Read one measured value, or None when the station has none."""
    if value_text.strip() in MISSING_VALUE_MARKERS:
        return None
    return float(value_text)


def _parse_values(row: Dict[str, str], parameter_names: Sequence[str]) -> Dict[str, Optional[float]]:
    """Read the value of each of the parameters from one row, None where the station has none."""
    parameter_to_value: Dict[str, Optional[float]] = {}
    for parameter in parameter_names:
        parameter_to_value[parameter] = _parse_value(row[parameter])
    return parameter_to_value


def _find_canton_name(abbreviation: str) -> str:
    """Find the full name of a canton, such as "Zurich" for "ZH"; an unknown abbreviation stays as it is."""
    return CANTON_ABBREVIATION_TO_NAME.get(abbreviation, abbreviation)


def _parse_data_since(date_text: str) -> date:
    """Read the start of a station's measurements in the station table, such as "01.01.1864"."""
    return datetime.strptime(date_text, "%d.%m.%Y").date()


def _parse_reference_timestamp(timestamp_text: str) -> datetime:
    """Read a now file or daily file timestamp such as "30.09.2026 07:40", which is always UTC."""
    return datetime.strptime(timestamp_text, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)


def _parse_stamp(stamp_text: str) -> datetime:
    """Read an all stations file timestamp such as "202609281020", which is always UTC."""
    return datetime.strptime(stamp_text, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


def _describe_days(first_day: date, last_day: date) -> str:
    """Write one day or a period of days for a message, such as "2026-11-04" or "2026-11-01 to 2026-11-04"."""
    if first_day == last_day:
        return first_day.isoformat()
    return f"{first_day.isoformat()} to {last_day.isoformat()}"


def _find_days_between(
    all_days: List[MeteoSwissStationMeasurements], first_day: date, last_day: date
) -> List[MeteoSwissStationMeasurements]:
    """Find the daily rows from first_day to last_day."""
    days_between = []
    for day_measurements in all_days:
        if first_day <= day_measurements.measured_at.date() <= last_day:
            days_between.append(day_measurements)
    return days_between


def _list_periods(first_day: date, last_day: date, today: date) -> List[str]:
    """List the daily files that hold the days, the older one first: historical before this year, recent in it."""
    this_year_start = date(today.year, 1, 1)
    periods = []
    if first_day < this_year_start:
        periods.append(HISTORICAL_DAYS)
    if last_day >= this_year_start:
        periods.append(RECENT_DAYS)
    return periods


class MeteoSwissMeasurementSource:
    """Read the latest SwissMetNet measurements and find the station for a place."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "measurements"
        self._abbreviation_to_station: Dict[str, MeteoSwissStation] = {}

    def _load_stations(self) -> Dict[str, MeteoSwissStation]:
        """Read the station table into memory once per process, keyed by station abbreviation."""
        if self._abbreviation_to_station:
            return self._abbreviation_to_station

        station_table_file = self.cache_dir / "ogd-smn_meta_stations.csv"
        station_table = ensure_recent_file(STATION_TABLE_URL, station_table_file, STATION_TABLE_MAX_AGE)
        abbreviation_to_station: Dict[str, MeteoSwissStation] = {}
        with open(station_table, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                data_since = _parse_data_since(row["station_data_since"])
                station = MeteoSwissStation(
                    abbr=row["station_abbr"],
                    name=row["station_name"],
                    canton=_find_canton_name(row["station_canton"]),
                    altitude_m=float(row["station_height_masl"]),
                    east_m=float(row["station_coordinates_lv95_east"]),
                    north_m=float(row["station_coordinates_lv95_north"]),
                    data_since=data_since,
                )
                abbreviation_to_station[station.abbr] = station
        # Published only once complete, so a parallel request never sees part of the table
        self._abbreviation_to_station = abbreviation_to_station
        logger.info(f"Loaded {len(abbreviation_to_station)} weather stations")
        return abbreviation_to_station

    def _read_station_measurements(self, station: MeteoSwissStation) -> List[MeteoSwissStationMeasurements]:
        """Read every row of a station's now file, the oldest first."""
        abbr = station.abbr.lower()
        station_now_values_url = STATION_NOW_VALUES_URL.format(abbr=abbr)
        station_now_values_file = self.cache_dir / f"ogd-smn_{abbr}_t_now.csv"
        station_now_values = ensure_recent_file(
            station_now_values_url, station_now_values_file, STATION_NOW_VALUES_MAX_AGE
        )

        all_measurements = []
        with open(station_now_values, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                measured_at = _parse_reference_timestamp(row["reference_timestamp"])
                parameter_to_value = _parse_values(row, parameters.ALL_NOW_PARAMETERS)
                measurements = MeteoSwissStationMeasurements(station, measured_at, parameter_to_value)
                all_measurements.append(measurements)
        return all_measurements

    def _read_station_days(self, station: MeteoSwissStation, period: str) -> List[MeteoSwissStationMeasurements]:
        """Read every row of one of a station's daily files, RECENT_DAYS or HISTORICAL_DAYS, the oldest first."""
        abbr = station.abbr.lower()
        station_days_url = STATION_DAYS_URL.format(abbr=abbr, period=period)
        station_days_file = self.cache_dir / f"ogd-smn_{abbr}_d_{period}.csv"
        station_days = ensure_recent_file(station_days_url, station_days_file, PERIOD_TO_MAX_AGE[period])

        all_days = []
        with open(station_days, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                # A daily row is stamped 00:00 UTC at the start of its day
                measured_at = _parse_reference_timestamp(row["reference_timestamp"])
                parameter_to_value = _parse_values(row, parameters.ALL_DAILY_PARAMETERS)
                day_measurements = MeteoSwissStationMeasurements(station, measured_at, parameter_to_value)
                all_days.append(day_measurements)
        return all_days

    def _rank_stations(self, place: SwisstopoPlace) -> List[MeteoSwissStation]:
        """Rank the stations for a place by distance plus METRES_PER_HEIGHT_METRE times the height gap."""
        abbreviation_to_station = self._load_stations()
        return sorted(
            abbreviation_to_station.values(), key=lambda station: calculate_weighted_distance_m(place, station)
        )

    def find_station_measurements(self, place: SwisstopoPlace) -> List[MeteoSwissStationMeasurements]:
        """
        Find the measurements of the station for a place, the oldest first.

        Stations are ranked by distance plus METRES_PER_HEIGHT_METRE times the height gap, and the
        first one that published a temperature in the last hour is taken. Raise CannotAnswerError
        when none of the first MAX_STATIONS_TRIED stations did.
        """
        ranked_stations = self._rank_stations(place)
        now = datetime.now(timezone.utc)
        for station in ranked_stations[:MAX_STATIONS_TRIED]:
            all_measurements = self._read_station_measurements(station)
            # Some stations measure only a few values, such as wind on a tower, and would answer
            # with almost every value None; a temperature marks a station that measures the usual set
            if not all_measurements or all_measurements[-1].parameter_to_value[parameters.TEMPERATURE] is None:
                logger.info(f"Station {station.abbr} has no current temperature, trying the next station")
                continue
            latest_measurements = all_measurements[-1]
            if now - latest_measurements.measured_at > LATEST_MEASUREMENTS_MAX_AGE:
                logger.info(f"Station {station.abbr} has no measurements in the last hour, trying the next station")
                continue
            return all_measurements

        raise CannotAnswerError(
            f"None of the {MAX_STATIONS_TRIED} stations tried for {place.display_name} published a "
            f"temperature in the last hour, try again later."
        )

    def find_station_days(
        self, place: SwisstopoPlace, first_day: date, last_day: date
    ) -> List[MeteoSwissStationMeasurements]:
        """
        Find the daily measurements of the station for a place from first_day to last_day, the oldest first.

        The days are UTC days. Stations are ranked as in find_station_measurements, and the first one
        with a temperature on one of the days is taken; a day it has no row for is left out. Raise
        CannotAnswerError for today or a later day, for days MeteoSwiss has not published yet, and when
        none of the first MAX_STATIONS_TRIED stations has a temperature on the days.
        """
        today = datetime.now(timezone.utc).date()
        if last_day >= today:
            unmeasured_day = max(first_day, today)
            raise CannotAnswerError(
                f"There are no daily measurements for {unmeasured_day.isoformat()} yet. MeteoSwiss publishes "
                f"the values of a day the next morning, so use measurements_current for now and forecast_daily "
                f"for today and the days ahead."
            )

        periods = _list_periods(first_day, last_day, today)
        described_days = _describe_days(first_day, last_day)
        ranked_stations = self._rank_stations(place)
        newest_published_days = []
        for station in ranked_stations[:MAX_STATIONS_TRIED]:
            # The station table says on which day each station started measuring. A station that started after
            # the asked days has no values for them, so it is skipped without downloading its files.
            if station.data_since > last_day:
                logger.info(f"Station {station.abbr} started on {station.data_since}, trying the next station")
                continue

            all_days = []
            for period in periods:
                period_days = self._read_station_days(station, period)
                all_days.extend(period_days)
            if all_days:
                newest_published_days.append(all_days[-1].measured_at.date())

            asked_days = _find_days_between(all_days, first_day, last_day)
            has_temperature = any(
                day_measurements.parameter_to_value[parameters.TEMPERATURE_DAY_MEAN] is not None
                for day_measurements in asked_days
            )
            if has_temperature:
                return asked_days
            logger.info(f"Station {station.abbr} has no temperature for {described_days}, trying the next station")

        # MeteoSwiss publishes the days of all stations together, so days after the newest row of every
        # station tried are not published yet
        if newest_published_days and max(newest_published_days) < first_day:
            raise CannotAnswerError(
                f"MeteoSwiss has not published the daily measurements for {described_days} yet, try again later."
            )
        raise CannotAnswerError(
            f"None of the {MAX_STATIONS_TRIED} stations tried for {place.display_name} has a daily temperature "
            f"for {described_days}."
        )

    def read_all_stations_latest_measurements(self) -> List[MeteoSwissStationMeasurements]:
        """Read the latest measurements of every station in the station table from the all stations file."""
        abbreviation_to_station = self._load_stations()
        all_stations_latest_values_file = self.cache_dir / "VQHA80.csv"
        all_stations_latest_values = ensure_recent_file(
            ALL_STATIONS_LATEST_VALUES_URL, all_stations_latest_values_file, ALL_STATIONS_LATEST_VALUES_MAX_AGE
        )

        all_measurements = []
        with open(all_stations_latest_values, newline="", encoding="latin-1") as file:
            for row in csv.DictReader(file, delimiter=";"):
                station = abbreviation_to_station.get(row["Station/Location"])
                if station is None:
                    continue  # a station without a row in the station table has no name and no canton
                measured_at = _parse_stamp(row["Date"])
                parameter_to_value = _parse_values(row, parameters.ALL_NOW_PARAMETERS)
                measurements = MeteoSwissStationMeasurements(station, measured_at, parameter_to_value)
                all_measurements.append(measurements)
        return all_measurements
