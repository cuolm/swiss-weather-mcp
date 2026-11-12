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
- A data inventory, ogd-smn_meta_datainventory.csv, with one row per station and
  parameter: the day it starts, and the day it ends when the station no longer measures it.
  About 75 stations measure the snow depth, htoauts0.
- The manual precipitation stations, here called manual sites, where an observer measures once
  a day, each morning. 262 of the 270 have measured the snow depth, 247 still do, half of them since
  1971 or earlier. The data inventory lists a parameter once per data owner, so a site whose
  measurements passed to SLF, such as Davos in 2006, has two rows for it. Their station table, ogd-nime_meta_stations.csv, data inventory and daily files,
  such as ogd-nime_dav_d_recent.csv, are built like those of SwissMetNet. A daily row holds the
  snow depth measured at 06:00 UTC on its day and the new snow of the 24 hours from then, so a
  day's new snow shows in the snow depth of the next morning. Some SwissMetNet stations, such as
  Davos, are manual sites too, under the same abbreviation.
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

    ogd-nime_dav_d_historical.csv  (daily file of the manual site Davos, 5 columns, the server reads 2 parameters)
    ┌──────────────────────────────────────────────────────────────┐
    │ station_abbr;reference_timestamp;rre150d0;hns000d0;hto000d0  │  station, day, rain, new snow, snow depth
    │ DAV;02.01.2025 00:00;2.4;5;37                                │  37 cm at 06:00 UTC, then 5 cm new snow
    │ DAV;03.01.2025 00:00;0;0;41                                  │  41 cm the next morning
    │ ...                                                          │  one row per day
    └──────────────────────────────────────────────────────────────┘

    ogd-smn_meta_stations.csv  (station table)
    ┌────────────────────────────────────────────────────────────────┐
    │ station_abbr;station_name;...;station_height_masl;...          │  header
    │ ARO;Arosa;...;1878.0;...;2771031.0;1184830.0;...               │  altitude, LV95 east, north
    │ SMA;Zürich / Fluntern;...;604.0;...;2685223.0;1248410.0;...    │
    │ ...                                                            │  about 160 rows
    └────────────────────────────────────────────────────────────────┘

How we use it, while the tools in current.py, history.py and snow.py choose the station:
1. Read the station table on the first question and keep it in memory for as long as
   the process runs; the file is downloaded first when it is missing or older than 7 days.
2. For the weather now, read a station's now file; the file is downloaded again when it is
   older than 5 minutes. Its last row holds the latest measurements, and its earlier rows
   show how the values changed.
3. For past days, read the daily files of a station that hold the days: the recent file
   for days of this year, downloaded again when older than 1 hour, and the historical file
   for earlier days, downloaded again when older than 30 days.
4. For the snow depth, read the data inventory on the first question and keep the stations
   that measure it now in memory; the file is downloaded first when it is missing or older
   than 7 days. A station's snow depth is the one of the last row of its now file, when it
   is at most 210 minutes old, as on SLF's map of measured values.
5. For the snow of past days, read the manual site table and, from the manual sites' data
   inventory, the first day each site measured the snow depth, on the first question, and keep
   both in memory; they are downloaded first when missing or older than 7 days. A manual site's
   daily files are read like those of a SwissMetNet station.

How we use it to compare all stations:
1. Read the all stations file; it is downloaded again when it is older than 5 minutes.
2. Join each row to its station by abbreviation. A station missing from the station
   table has no name and no canton, so it is left out.

    <cache dir>/measurements/
    ├── ogd-smn_meta_stations.csv      (station table, downloaded again when older than 7 days)
    ├── ogd-smn_meta_datainventory.csv (data inventory, downloaded again when older than 7 days)
    ├── ogd-smn_sma_t_now.csv          (now file per station, downloaded again after 5 minutes)
    ├── ogd-smn_sma_d_recent.csv       (days of this year per station, downloaded again after 1 hour)
    ├── ogd-smn_sma_d_historical.csv   (earlier days per station, downloaded again after 30 days)
    ├── VQHA80.csv                     (all stations, downloaded again after 5 minutes)
    ├── ogd-nime_meta_stations.csv     (manual site table, downloaded again when older than 7 days)
    ├── ogd-nime_meta_datainventory.csv (manual site data inventory, downloaded again when older than 7 days)
    ├── ogd-nime_dav_d_recent.csv      (days of this year per manual site, downloaded again after 1 hour)
    └── ogd-nime_dav_d_historical.csv  (earlier days per manual site, downloaded again after 30 days)
"""
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Set

from . import parameters
from ..downloads import read_recent_csv_rows
from .snow_values import SNOW_DEPTH_MAX_AGE, SnowDepth

logger = logging.getLogger(__name__)

STATION_TABLE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/ogd-smn_meta_stations.csv"
DATA_INVENTORY_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/ogd-smn_meta_datainventory.csv"
STATION_NOW_VALUES_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/{abbr}/ogd-smn_{abbr}_t_now.csv"
# The period is RECENT_DAYS or HISTORICAL_DAYS
STATION_DAILY_VALUES_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/{abbr}/ogd-smn_{abbr}_d_{period}.csv"
ALL_STATIONS_LATEST_VALUES_URL = "https://data.geo.admin.ch/ch.meteoschweiz.messwerte-aktuell/VQHA80.csv"
MANUAL_SITE_TABLE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-nime/ogd-nime_meta_stations.csv"
MANUAL_SITE_INVENTORY_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-nime/ogd-nime_meta_datainventory.csv"
MANUAL_SITE_DAILY_VALUES_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-nime/{abbr}/ogd-nime_{abbr}_d_{period}.csv"
RECENT_DAYS = "recent"
HISTORICAL_DAYS = "historical"

STATION_TABLE_MAX_AGE = timedelta(days=7)
DATA_INVENTORY_MAX_AGE = timedelta(days=7)
# Shorter than MeteoSwiss's 10-minute update, so a new file is used at most 5 minutes after it appears
STATION_NOW_VALUES_MAX_AGE = timedelta(minutes=5)
ALL_STATIONS_LATEST_VALUES_MAX_AGE = timedelta(minutes=5)
MANUAL_SITE_TABLE_MAX_AGE = timedelta(days=7)
MANUAL_SITE_INVENTORY_MAX_AGE = timedelta(days=7)
# MeteoSwiss updates the recent daily files twice a day, and the historical ones once a year
PERIOD_TO_MAX_AGE = {
    RECENT_DAYS: timedelta(hours=1),
    HISTORICAL_DAYS: timedelta(days=30),
}

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
    """A SwissMetNet weather station or a manual site, as their station tables describe them."""
    abbr: str
    name: str
    canton: str  # the full name, such as "Zurich"
    altitude_m: float
    east_m: float   # LV95, the Swiss grid in metres
    north_m: float  # LV95
    data_since: date  # the first day of any of its measurements; its temperature may start years later
    is_manual_site: bool  # an observer measures there once a day; otherwise it is an automatic SwissMetNet station

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


def _parse_station(row: Dict[str, str], is_manual_site: bool) -> MeteoSwissStation:
    """Turn one row of a station table into a station."""
    data_since = _parse_data_since(row["station_data_since"])
    canton = _find_canton_name(row["station_canton"])
    return MeteoSwissStation(
        abbr=row["station_abbr"],
        name=row["station_name"],
        canton=canton,
        altitude_m=float(row["station_height_masl"]),
        east_m=float(row["station_coordinates_lv95_east"]),
        north_m=float(row["station_coordinates_lv95_north"]),
        data_since=data_since,
        is_manual_site=is_manual_site,
    )


def _parse_reference_timestamp(timestamp_text: str) -> datetime:
    """Read a now file or daily file timestamp such as "30.09.2026 07:40", which is always UTC."""
    return datetime.strptime(timestamp_text, "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)


def _parse_all_stations_timestamp(stamp_text: str) -> datetime:
    """Read an all stations file timestamp such as "202609281020", which is always UTC."""
    return datetime.strptime(stamp_text, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


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
    """
    Read the SwissMetNet stations and their measurements: now, on past days, the snow depth and all stations.

    Also read the manual sites and their snow on past days.
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "measurements"
        self._abbreviation_to_station: Dict[str, MeteoSwissStation] = {}
        self._snow_station_abbreviations: Set[str] = set()
        self._abbreviation_to_manual_site: Dict[str, MeteoSwissStation] = {}
        self._abbreviation_to_first_snow_day: Dict[str, date] = {}

    def _load_stations(self) -> Dict[str, MeteoSwissStation]:
        """Read the station table into memory once per process, keyed by station abbreviation."""
        if self._abbreviation_to_station:
            return self._abbreviation_to_station

        station_table_file = self.cache_dir / "ogd-smn_meta_stations.csv"
        station_table_rows = read_recent_csv_rows(STATION_TABLE_URL, station_table_file, STATION_TABLE_MAX_AGE)
        abbreviation_to_station: Dict[str, MeteoSwissStation] = {}
        for row in station_table_rows:
            station = _parse_station(row, is_manual_site=False)
            abbreviation_to_station[station.abbr] = station
        # Published only once complete, so a parallel request never sees part of the table
        self._abbreviation_to_station = abbreviation_to_station
        logger.info(f"Loaded {len(abbreviation_to_station)} weather stations")
        return abbreviation_to_station

    def _load_snow_station_abbreviations(self) -> Set[str]:
        """Read once per process which stations measure the snow depth now, from the data inventory."""
        if self._snow_station_abbreviations:
            return self._snow_station_abbreviations

        data_inventory_file = self.cache_dir / "ogd-smn_meta_datainventory.csv"
        data_inventory_rows = read_recent_csv_rows(DATA_INVENTORY_URL, data_inventory_file, DATA_INVENTORY_MAX_AGE)
        snow_station_abbreviations: Set[str] = set()
        for row in data_inventory_rows:
            # A parameter with an end day is no longer measured
            if row["parameter_shortname"] == parameters.SNOW_DEPTH and not row["data_till"]:
                snow_station_abbreviations.add(row["station_abbr"])
        self._snow_station_abbreviations = snow_station_abbreviations
        logger.info(f"Loaded {len(snow_station_abbreviations)} stations that measure the snow depth")
        return snow_station_abbreviations

    def _load_manual_sites(self) -> Dict[str, MeteoSwissStation]:
        """Read the manual site table into memory once per process, keyed by station abbreviation."""
        if self._abbreviation_to_manual_site:
            return self._abbreviation_to_manual_site

        manual_site_table_file = self.cache_dir / "ogd-nime_meta_stations.csv"
        manual_site_table_rows = read_recent_csv_rows(
            MANUAL_SITE_TABLE_URL, manual_site_table_file, MANUAL_SITE_TABLE_MAX_AGE
        )
        abbreviation_to_manual_site: Dict[str, MeteoSwissStation] = {}
        for row in manual_site_table_rows:
            manual_site = _parse_station(row, is_manual_site=True)
            abbreviation_to_manual_site[manual_site.abbr] = manual_site
        # Published only once complete, so a parallel request never sees part of the table
        self._abbreviation_to_manual_site = abbreviation_to_manual_site
        logger.info(f"Loaded {len(abbreviation_to_manual_site)} manual sites")
        return abbreviation_to_manual_site

    def _load_manual_site_first_snow_days(self) -> Dict[str, date]:
        """Read once per process the first day each manual site measured the snow depth, from its data inventory."""
        if self._abbreviation_to_first_snow_day:
            return self._abbreviation_to_first_snow_day

        manual_site_inventory_file = self.cache_dir / "ogd-nime_meta_datainventory.csv"
        manual_site_inventory_rows = read_recent_csv_rows(
            MANUAL_SITE_INVENTORY_URL, manual_site_inventory_file, MANUAL_SITE_INVENTORY_MAX_AGE
        )
        abbreviation_to_first_snow_day: Dict[str, date] = {}
        for row in manual_site_inventory_rows:
            if row["parameter_shortname"] == parameters.SNOW_DEPTH_DAY:
                # Such as "01.01.1931 00:00". A site has one row per data owner, and its earliest day counts.
                first_snow_day = _parse_reference_timestamp(row["data_since"]).date()
                earlier_first_snow_day = abbreviation_to_first_snow_day.get(row["station_abbr"], first_snow_day)
                abbreviation_to_first_snow_day[row["station_abbr"]] = min(first_snow_day, earlier_first_snow_day)
        self._abbreviation_to_first_snow_day = abbreviation_to_first_snow_day
        logger.info(f"Loaded the first snow depth day of {len(abbreviation_to_first_snow_day)} manual sites")
        return abbreviation_to_first_snow_day

    def read_station_measurements(self, station: MeteoSwissStation) -> List[MeteoSwissStationMeasurements]:
        """Read every row of a station's now file, the oldest first."""
        abbr = station.abbr.lower()
        station_now_values_url = STATION_NOW_VALUES_URL.format(abbr=abbr)
        station_now_values_file = self.cache_dir / f"ogd-smn_{abbr}_t_now.csv"
        station_now_values_rows = read_recent_csv_rows(
            station_now_values_url, station_now_values_file, STATION_NOW_VALUES_MAX_AGE
        )

        all_measurements = []
        for row in station_now_values_rows:
            measured_at = _parse_reference_timestamp(row["reference_timestamp"])
            parameter_to_value = _parse_values(row, parameters.NOW_FILE_PARAMETERS)
            measurements = MeteoSwissStationMeasurements(station, measured_at, parameter_to_value)
            all_measurements.append(measurements)
        return all_measurements

    def _read_station_daily_file(self, station: MeteoSwissStation, period: str) -> List[MeteoSwissStationMeasurements]:
        """
        Read every row of one of a station's daily files, RECENT_DAYS or HISTORICAL_DAYS, the oldest first.

        A manual site's files hold only its snow.
        """
        abbr = station.abbr.lower()
        daily_parameters: Sequence[str]
        if station.is_manual_site:
            station_daily_values_url = MANUAL_SITE_DAILY_VALUES_URL.format(abbr=abbr, period=period)
            station_daily_values_file = self.cache_dir / f"ogd-nime_{abbr}_d_{period}.csv"
            daily_parameters = parameters.ALL_MANUAL_SITE_DAILY_PARAMETERS
        else:
            station_daily_values_url = STATION_DAILY_VALUES_URL.format(abbr=abbr, period=period)
            station_daily_values_file = self.cache_dir / f"ogd-smn_{abbr}_d_{period}.csv"
            daily_parameters = parameters.ALL_DAILY_PARAMETERS
        station_daily_values_rows = read_recent_csv_rows(
            station_daily_values_url, station_daily_values_file, PERIOD_TO_MAX_AGE[period]
        )

        all_days = []
        for row in station_daily_values_rows:
            # A daily row is stamped 00:00 UTC at the start of its day
            measured_at = _parse_reference_timestamp(row["reference_timestamp"])
            parameter_to_value = _parse_values(row, daily_parameters)
            day_measurements = MeteoSwissStationMeasurements(station, measured_at, parameter_to_value)
            all_days.append(day_measurements)
        return all_days

    def list_stations(self) -> List[MeteoSwissStation]:
        """List the stations of the station table."""
        abbreviation_to_station = self._load_stations()
        return list(abbreviation_to_station.values())

    def read_station_daily_measurements(
        self, station: MeteoSwissStation, first_day: date, last_day: date
    ) -> List[MeteoSwissStationMeasurements]:
        """
        Read the rows of a station's daily files that hold the days from first_day to last_day, the oldest first.

        The files may hold other days too; a day MeteoSwiss has not published yet is missing.
        """
        today = datetime.now(timezone.utc).date()
        periods = _list_periods(first_day, last_day, today)
        all_days = []
        for period in periods:
            period_days = self._read_station_daily_file(station, period)
            all_days.extend(period_days)
        return all_days

    def list_snow_stations(self) -> List[MeteoSwissStation]:
        """List the stations that measure the snow depth now."""
        abbreviation_to_station = self._load_stations()
        snow_station_abbreviations = self._load_snow_station_abbreviations()
        snow_stations = []
        for station in abbreviation_to_station.values():
            if station.abbr in snow_station_abbreviations:
                snow_stations.append(station)
        return snow_stations

    def read_snow_depth(self, station: MeteoSwissStation) -> Optional[SnowDepth]:
        """Read a station's current snow depth, or None when it has none or it is older than SNOW_DEPTH_MAX_AGE."""
        all_measurements = self.read_station_measurements(station)
        if not all_measurements:
            return None
        latest_measurements = all_measurements[-1]
        snow_depth_cm = latest_measurements.parameter_to_value[parameters.SNOW_DEPTH]
        now = datetime.now(timezone.utc)
        if snow_depth_cm is None or now - latest_measurements.measured_at > SNOW_DEPTH_MAX_AGE:
            return None
        return SnowDepth(station, latest_measurements.measured_at, snow_depth_cm)

    def list_manual_sites(self, last_day: date) -> List[MeteoSwissStation]:
        """List the manual sites that started measuring the snow depth by last_day."""
        abbreviation_to_manual_site = self._load_manual_sites()
        abbreviation_to_first_snow_day = self._load_manual_site_first_snow_days()
        manual_sites = []
        for manual_site in abbreviation_to_manual_site.values():
            # A site without a first snow day measures only the rain
            first_snow_day = abbreviation_to_first_snow_day.get(manual_site.abbr)
            if first_snow_day is None:
                continue
            if first_snow_day <= last_day:
                manual_sites.append(manual_site)
        return manual_sites

    def read_all_stations_latest_measurements(self) -> List[MeteoSwissStationMeasurements]:
        """Read the latest measurements of every station in the station table from the all stations file."""
        abbreviation_to_station = self._load_stations()
        all_stations_latest_values_file = self.cache_dir / "VQHA80.csv"
        all_stations_latest_values_rows = read_recent_csv_rows(
            ALL_STATIONS_LATEST_VALUES_URL, all_stations_latest_values_file, ALL_STATIONS_LATEST_VALUES_MAX_AGE
        )

        all_measurements = []
        for row in all_stations_latest_values_rows:
            station = abbreviation_to_station.get(row["Station/Location"])
            if station is None:
                continue  # a station without a row in the station table has no name and no canton
            measured_at = _parse_all_stations_timestamp(row["Date"])
            parameter_to_value = _parse_values(row, parameters.ALL_NOW_PARAMETERS)
            measurements = MeteoSwissStationMeasurements(station, measured_at, parameter_to_value)
            all_measurements.append(measurements)
        return all_measurements
