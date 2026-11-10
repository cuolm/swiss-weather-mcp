"""
Read the latest measurements of the SLF stations from the SLF measurement API: the automatic
IMIS weather stations, and the manual measuring sites, which the API calls study plots, where an
observer measures the snow by hand.

What SLF publishes:
- The measurements of an IMIS station, such as imis/station/DAV2/measurements, with
  period_in_days=1 the rows of the last 24 hours. A row every 30 minutes, at :00 and
  :30 UTC, holds the means and maxima of the 30 minutes up to its time, and the snow depth
  at that time. SLF adds a row about 10 minutes after its time. A value a station does not
  measure is null.
- The daily snow of all IMIS snow stations, imis/daily-snow, with period_in_days=3 the
  last 4 days, about 130 stations each. Each row holds the median snow depth of the 24
  hours up to 06:00 UTC and the new snow of those 24 hours, which SLF calculates from the
  snow depth with its SNOWPACK model. The rows are stamped with their day at 00:00.
- The measurements of a manual site, such as study-plot/station/5DF0/measurements, with
  period_in_days=7 one row per day for the last 7 days, today's only once it is measured,
  stamped 06:00 UTC. An observer measures the snow depth and the new snow of the last 24
  hours by hand each morning, from November to April.
- A station list each, imis/stations and study-plot/stations, with one object per station:
  its name, altitude, position in WGS84 latitude and longitude and canton, and for an IMIS
  station its type. About 200 IMIS stations, most of them between 1300 and 3300 m, and
  about 100 manual sites, in Switzerland and Liechtenstein.
- All answers are JSON. A station code, such as DAV2, links a station list to the
  station's measurements.

    imis/station/DAV2/measurements?period_in_days=1  (measurements of Bärentälli, 15 fields, the server reads 6 values)
    ┌────────────────────────────────────────────────────────────────────┐
    │ [{"station_code": "DAV2", "measure_date": "2026-11-09T12:00:00Z",  │  station, time in UTC
    │   "TA_30MIN_MEAN": -0.338, "RH_30MIN_MEAN": 79.5,                  │  -0.3 °C, 79.5 %
    │   "VW_30MIN_MEAN": 0.652, "VW_30MIN_MAX": 2.933,                   │  wind and 5-second peak in m/s
    │   "DW_30MIN_MEAN": 58.52, "HS": 1.0, ...},                         │  direction, snow depth in cm
    │  ...]                                                              │  one row every 30 minutes
    └────────────────────────────────────────────────────────────────────┘

    imis/daily-snow?period_in_days=3  (daily snow of all IMIS snow stations)
    ┌────────────────────────────────────────────────────────────────────┐
    │ [{"station_code": "ADE2", "measure_date": "2026-11-10T00:00:00",   │  station, day
    │   "HS": 1.0, "HN_1D": 0.0},                                        │  snow depth, new snow in cm
    │  ...]                                                              │  one row per station and day
    └────────────────────────────────────────────────────────────────────┘

    study-plot/station/5DF0/measurements?period_in_days=7  (measurements of the manual site Davos Flüelastr., 5 fields, the server reads 2 values)
    ┌────────────────────────────────────────────────────────────────────┐
    │ [{"station_code": "5DF0", "measure_date": "2026-11-10T06:00:00Z",  │  station, morning in UTC
    │   "HS": 0.0, "HN_1D": 0.0, "HNW_1D": 0.0},                         │  snow depth, new snow in cm
    │  ...]                                                              │  one row per day
    └────────────────────────────────────────────────────────────────────┘

    imis/stations  (station list)
    ┌────────────────────────────────────────────────────────────────────────┐
    │ [{"code": "DAV2", "label": "Bärentälli", "lon": 9.8194108372,          │  code, name, WGS84 position
    │   "lat": 46.6988884749, "elevation": 2558.0, "type": "SNOW_FLAT", ...},│  altitude, type
    │  ...]                                                                  │  about 200 stations
    └────────────────────────────────────────────────────────────────────────┘

How we use it for the weather at one place:
1. Read the station list on the first question and keep it in memory for as long as
   the process runs; the file is downloaded first when it is missing or older than 7 days.
   Each WGS84 position is turned into LV95 with swisstopo's approximate formulas.
2. Rank the stations that suit the place better than the MeteoSwiss station that answers,
   by their distance plus METRES_PER_HEIGHT_METRE times their height gap, as for the
   MeteoSwiss stations. The others would say less about the place than it does.
3. Read the measurements of the first station; the file is downloaded again when it is
   older than 5 minutes. Its last row holds the latest measurements. The wind speeds are
   turned from m/s into km/h, the unit MeteoSwiss uses.
4. Take the next station when the latest measurements have no temperature or are older
   than 1 hour, at most 3 stations. A station without a temperature would answer without
   the main value, and a station that stopped publishing would answer with old values.

How we use it for the snow at one place:
1. List the IMIS stations that have daily snow, and the manual sites; both lists are kept as
   the station list. The daily snow file is downloaded again when it is older than 1 hour.
   The service ranks them together with the MeteoSwiss snow stations.
2. The snow depth of an IMIS station is the one of its last 30-minute row, when it is at
   most 210 minutes old, as on SLF's map of measured values. The snow depth of a manual site
   is the one of its newest day, when it is from today or yesterday; today's arrives only
   in the morning. A manual site's file is downloaded again when it is older than 1 hour.
3. The new snow is the one of a station's newest day, when it is from today or yesterday,
   and the new snow of the 3 days up to it is their sum, as SLF adds them for its maps.

    <cache dir>/slf/
    ├── imis_stations.json               (station list, downloaded again when older than 7 days)
    ├── imis_dav2_measurements.json      (measurements per station, downloaded again after 5 minutes)
    ├── imis_daily_snow.json             (daily snow of all IMIS snow stations, downloaded again after 1 hour)
    ├── manual_sites.json                (manual site list, downloaded again when older than 7 days)
    └── manual_site_5df0_daily_snow.json (daily snow per manual site, downloaded again after 1 hour)
"""
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

from ..coordinates import calculate_lv95_east_m, calculate_lv95_north_m
from ..distance import rank_by_weighted_distance
from ..downloads import ensure_recent_file
from ..places import SwisstopoPlace
from .snow_values import SNOW_DEPTH_MAX_AGE, NewSnow, SnowDepth

logger = logging.getLogger(__name__)

STATION_LIST_URL = "https://measurement-api.slf.ch/public/api/imis/stations"
STATION_MEASUREMENTS_URL = "https://measurement-api.slf.ch/public/api/imis/station/{code}/measurements?period_in_days=1"
IMIS_DAILY_SNOW_URL = "https://measurement-api.slf.ch/public/api/imis/daily-snow?period_in_days=3"
MANUAL_SITE_LIST_URL = "https://measurement-api.slf.ch/public/api/study-plot/stations"
# 3 days would be today and the 2 days before, too few for 3 days of new snow before today's reading
MANUAL_SITE_DAILY_SNOW_URL = "https://measurement-api.slf.ch/public/api/study-plot/station/{code}/measurements?period_in_days=7"

# SLF says nothing on how often the list changes, so it is kept as long as the MeteoSwiss station table
STATION_LIST_MAX_AGE = timedelta(days=7)
MANUAL_SITE_LIST_MAX_AGE = timedelta(days=7)
# SLF's own server keeps an answer about 5 minutes
STATION_MEASUREMENTS_MAX_AGE = timedelta(minutes=5)
# The daily values arrive once in the morning, so a file is at most 1 hour behind
IMIS_DAILY_SNOW_MAX_AGE = timedelta(hours=1)
MANUAL_SITE_DAILY_SNOW_MAX_AGE = timedelta(hours=1)
# Older latest measurements mean the station stopped publishing
LATEST_MEASUREMENTS_MAX_AGE = timedelta(hours=1)
# A daily value counts when it is from today or yesterday, as today's arrives only in the morning
DAILY_VALUE_MAX_AGE = timedelta(days=1)
NEW_SNOW_DAYS = 3
# SLF gives the daily IMIS values "at 6 UTC", but stamps them with their day at 00:00 without a time zone
DAILY_SNOW_TIME = time(6, tzinfo=timezone.utc)
MAX_STATIONS_TRIED = 3

# The SLF description of each value, from https://measurement-api.slf.ch/openapi.json
TEMPERATURE = "TA_30MIN_MEAN"     # air temperature, mean over last 30 minute, °C
HUMIDITY = "RH_30MIN_MEAN"        # relative humidity, mean over last 30 minutes, %
WIND_SPEED = "VW_30MIN_MEAN"      # wind speed, vectorial mean over last 30 minutes, m/s
WIND_GUSTS = "VW_30MIN_MAX"       # wind speed, max 5sec measurement within last 30 minutes, m/s
WIND_DIRECTION = "DW_30MIN_MEAN"  # wind direction, vectorial mean over last 30 minutes, °
SNOW_DEPTH = "HS"                 # height of snowpack, total thickness of snowpack, instantaneous, cm
NEW_SNOW = "HN_1D"                # height of new snow during the last 24 hours, cm
# In the daily IMIS snow, HS is the "median over last 24h at 6 UTC" and HN_1D "modeled by SNOWPACK at 6 UTC",
# https://measurement-data.slf.ch/

KMH_PER_M_PER_S = 3.6



class SLFStation(NamedTuple):
    """An IMIS station or a manual site as SLF's station lists describe it."""
    code: str
    name: str
    altitude_m: float
    east_m: float   # LV95, the Swiss grid in metres, turned from the WGS84 position SLF publishes
    north_m: float  # LV95
    is_manual_site: bool  # an observer measures there by hand; otherwise it is an automatic IMIS station

    @property
    def display_name(self) -> str:
        """The name shown to a reader, with the altitude, such as "Bärentälli (2558 m)"."""
        return f"{self.name} ({self.altitude_m:.0f} m)"


class SLFStationMeasurements(NamedTuple):
    """The latest measurements of one IMIS station, over the 30 minutes up to measured_at; None where it has no value."""
    station: SLFStation
    measured_at: datetime
    temperature_c: Optional[float]
    humidity_percent: Optional[float]
    wind_speed_kmh: Optional[float]
    strongest_gust_kmh: Optional[float]  # the highest 5-second wind
    wind_direction_degrees: Optional[float]
    snow_depth_cm: Optional[float]  # at measured_at


class SLFDailySnowMeasurements(NamedTuple):
    """The snow of one day at an IMIS station or a manual site, stamped 06:00 UTC; None where it has no value."""
    measured_at: datetime
    snow_depth_cm: Optional[float]
    new_snow_cm: Optional[float]  # of the 24 hours up to measured_at


def _parse_station(station_object: Dict[str, Any], is_manual_site: bool) -> SLFStation:
    """Turn one object of a station list into a station on LV95."""
    latitude_deg = station_object["lat"]
    longitude_deg = station_object["lon"]
    return SLFStation(
        code=station_object["code"],
        name=station_object["label"],
        altitude_m=station_object["elevation"],
        east_m=calculate_lv95_east_m(latitude_deg, longitude_deg),
        north_m=calculate_lv95_north_m(latitude_deg, longitude_deg),
        is_manual_site=is_manual_site,
    )


def _convert_m_per_s_to_kmh(speed_m_per_s: Optional[float]) -> Optional[float]:
    """Turn a speed in m/s into km/h, or return None for None."""
    if speed_m_per_s is None:
        return None
    return speed_m_per_s * KMH_PER_M_PER_S


def _parse_station_measurements(station: SLFStation, row: Dict[str, Any]) -> SLFStationMeasurements:
    """Read the values the server uses from one row of a station's measurements."""
    # Such as "2026-11-09T12:00:00Z", which fromisoformat reads as UTC
    measured_at = datetime.fromisoformat(row["measure_date"])
    return SLFStationMeasurements(
        station=station,
        measured_at=measured_at,
        temperature_c=row[TEMPERATURE],
        humidity_percent=row[HUMIDITY],
        wind_speed_kmh=_convert_m_per_s_to_kmh(row[WIND_SPEED]),
        strongest_gust_kmh=_convert_m_per_s_to_kmh(row[WIND_GUSTS]),
        wind_direction_degrees=row[WIND_DIRECTION],
        snow_depth_cm=row[SNOW_DEPTH],
    )


def _parse_imis_daily_snow(row: Dict[str, Any]) -> SLFDailySnowMeasurements:
    """Read one row of the IMIS daily snow; one stamped "2026-11-10T00:00:00" holds the values of 06:00 UTC that day."""
    day = datetime.fromisoformat(row["measure_date"]).date()
    measured_at = datetime.combine(day, DAILY_SNOW_TIME)
    return SLFDailySnowMeasurements(measured_at, row[SNOW_DEPTH], row[NEW_SNOW])


def _parse_manual_site_daily_snow(row: Dict[str, Any]) -> SLFDailySnowMeasurements:
    """Read one row of a manual site's daily snow, stamped such as "2026-11-10T06:00:00Z"."""
    measured_at = datetime.fromisoformat(row["measure_date"])
    return SLFDailySnowMeasurements(measured_at, row[SNOW_DEPTH], row[NEW_SNOW])


def _is_recent_day(day_measurements: SLFDailySnowMeasurements, today: date) -> bool:
    """Tell whether a daily value is from today or yesterday."""
    return day_measurements.measured_at.date() >= today - DAILY_VALUE_MAX_AGE


def _sum_new_snow(all_days: List[SLFDailySnowMeasurements], last_day: date) -> Optional[float]:
    """Return the new snow of the NEW_SNOW_DAYS days up to last_day, or None when one of them has no value."""
    day_to_new_snow_cm: Dict[date, Optional[float]] = {}
    for day_measurements in all_days:
        day_to_new_snow_cm[day_measurements.measured_at.date()] = day_measurements.new_snow_cm

    total_cm = 0.0
    for days_back in range(NEW_SNOW_DAYS):
        day = last_day - timedelta(days=days_back)
        new_snow_cm = day_to_new_snow_cm.get(day)
        if new_snow_cm is None:
            return None
        total_cm += new_snow_cm
    # Rounded, as adding tenths of a centimetre leaves float noise such as 0.30000000000000004
    return round(total_cm, 1)


def _calculate_new_snow(
    station: SLFStation, all_days: List[SLFDailySnowMeasurements], today: date
) -> Optional[NewSnow]:
    """
    Calculate the new snow of the newest day, or None when it has none or is older than yesterday.

    all_days come oldest first.
    """
    if not all_days:
        return None
    newest_day = all_days[-1]
    if newest_day.new_snow_cm is None or not _is_recent_day(newest_day, today):
        return None
    newest_date = newest_day.measured_at.date()
    new_snow_3_days_cm = _sum_new_snow(all_days, newest_date)
    return NewSnow(station, newest_day.measured_at, newest_day.new_snow_cm, new_snow_3_days_cm)


class SLFMeasurementSource:
    """Read the latest SLF measurements: the weather of a station that suits a place better, and the snow."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "slf"
        self._stations: List[SLFStation] = []
        self._manual_sites: List[SLFStation] = []

    def _load_stations(self) -> List[SLFStation]:
        """Read the IMIS station list into memory once per process."""
        if self._stations:
            return self._stations

        station_list_file = self.cache_dir / "imis_stations.json"
        station_list = ensure_recent_file(STATION_LIST_URL, station_list_file, STATION_LIST_MAX_AGE)
        with open(station_list, encoding="utf-8") as file:
            station_objects = json.load(file)
        stations = []
        for station_object in station_objects:
            station = _parse_station(station_object, is_manual_site=False)
            stations.append(station)
        # Published only once complete, so a parallel request never sees part of the list
        self._stations = stations
        logger.info(f"Loaded {len(stations)} SLF stations")
        return stations

    def _load_manual_sites(self) -> List[SLFStation]:
        """Read the manual site list into memory once per process."""
        if self._manual_sites:
            return self._manual_sites

        manual_site_list_file = self.cache_dir / "manual_sites.json"
        manual_site_list = ensure_recent_file(MANUAL_SITE_LIST_URL, manual_site_list_file, MANUAL_SITE_LIST_MAX_AGE)
        with open(manual_site_list, encoding="utf-8") as file:
            station_objects = json.load(file)
        manual_sites = []
        for station_object in station_objects:
            manual_site = _parse_station(station_object, is_manual_site=True)
            manual_sites.append(manual_site)
        # Published only once complete, so a parallel request never sees part of the list
        self._manual_sites = manual_sites
        logger.info(f"Loaded {len(manual_sites)} SLF manual sites")
        return manual_sites

    def _read_latest_station_measurements(self, station: SLFStation) -> Optional[SLFStationMeasurements]:
        """Read the last row of a station's measurements, or None when it has no rows."""
        code = station.code.lower()
        station_measurements_url = STATION_MEASUREMENTS_URL.format(code=station.code)
        station_measurements_file = self.cache_dir / f"imis_{code}_measurements.json"
        station_measurements = ensure_recent_file(
            station_measurements_url, station_measurements_file, STATION_MEASUREMENTS_MAX_AGE
        )
        with open(station_measurements, encoding="utf-8") as file:
            rows = json.load(file)
        if not rows:
            return None
        return _parse_station_measurements(station, rows[-1])

    def _read_all_imis_stations_daily_snow(self) -> Dict[str, List[SLFDailySnowMeasurements]]:
        """Read the daily snow of every IMIS snow station, keyed by station code, the oldest day first."""
        daily_snow_file = self.cache_dir / "imis_daily_snow.json"
        daily_snow = ensure_recent_file(IMIS_DAILY_SNOW_URL, daily_snow_file, IMIS_DAILY_SNOW_MAX_AGE)
        with open(daily_snow, encoding="utf-8") as file:
            rows = json.load(file)

        station_code_to_daily_snow: Dict[str, List[SLFDailySnowMeasurements]] = {}
        for row in rows:
            day_measurements = _parse_imis_daily_snow(row)
            station_code_to_daily_snow.setdefault(row["station_code"], []).append(day_measurements)
        # The rows come grouped by day, the newest first
        for all_days in station_code_to_daily_snow.values():
            all_days.sort(key=lambda day_measurements: day_measurements.measured_at)
        return station_code_to_daily_snow

    def _read_manual_site_daily_snow(self, manual_site: SLFStation) -> List[SLFDailySnowMeasurements]:
        """Read the daily snow of a manual site, the oldest day first."""
        code = manual_site.code.lower()
        manual_site_daily_snow_url = MANUAL_SITE_DAILY_SNOW_URL.format(code=manual_site.code)
        manual_site_daily_snow_file = self.cache_dir / f"manual_site_{code}_daily_snow.json"
        manual_site_daily_snow = ensure_recent_file(
            manual_site_daily_snow_url, manual_site_daily_snow_file, MANUAL_SITE_DAILY_SNOW_MAX_AGE
        )
        with open(manual_site_daily_snow, encoding="utf-8") as file:
            rows = json.load(file)

        all_days = []
        for row in rows:
            day_measurements = _parse_manual_site_daily_snow(row)
            all_days.append(day_measurements)
        all_days.sort(key=lambda day_measurements: day_measurements.measured_at)
        return all_days

    def _read_imis_snow_depth(self, station: SLFStation) -> Optional[SnowDepth]:
        """Read an IMIS station's latest snow depth, or None when it is missing or older than SNOW_DEPTH_MAX_AGE."""
        latest_measurements = self._read_latest_station_measurements(station)
        if latest_measurements is None or latest_measurements.snow_depth_cm is None:
            return None
        now = datetime.now(timezone.utc)
        if now - latest_measurements.measured_at > SNOW_DEPTH_MAX_AGE:
            return None
        return SnowDepth(station, latest_measurements.measured_at, latest_measurements.snow_depth_cm)

    def _read_manual_site_snow_depth(self, manual_site: SLFStation) -> Optional[SnowDepth]:
        """Read the snow depth of a manual site's newest day, or None when it has none from today or yesterday."""
        all_days = self._read_manual_site_daily_snow(manual_site)
        if not all_days:
            return None
        newest_day = all_days[-1]
        today = datetime.now(timezone.utc).date()
        if newest_day.snow_depth_cm is None or not _is_recent_day(newest_day, today):
            return None
        return SnowDepth(manual_site, newest_day.measured_at, newest_day.snow_depth_cm)

    def find_closer_slf_station_measurements(
        self, place: SwisstopoPlace, meteoswiss_station_weighted_distance_m: float
    ) -> Optional[SLFStationMeasurements]:
        """
        Find the latest measurements of an SLF station that suits a place better than the MeteoSwiss station.

        meteoswiss_station_weighted_distance_m is the MeteoSwiss station's distance to the place plus
        METRES_PER_HEIGHT_METRE times its height gap, from calculate_weighted_distance_m. Only stations
        with a smaller or equal one are tried, the best first, and the first one that published a temperature
        in the last hour is taken. Return None when there is no such station among the first MAX_STATIONS_TRIED.
        """
        slf_stations = self._load_stations()
        ranked_slf_stations = rank_by_weighted_distance(place, slf_stations, meteoswiss_station_weighted_distance_m)
        now = datetime.now(timezone.utc)
        for station in ranked_slf_stations[:MAX_STATIONS_TRIED]:
            latest_measurements = self._read_latest_station_measurements(station)
            if latest_measurements is None or latest_measurements.temperature_c is None:
                logger.info(f"SLF station {station.code} has no current temperature, trying the next station")
                continue
            if now - latest_measurements.measured_at > LATEST_MEASUREMENTS_MAX_AGE:
                logger.info(f"SLF station {station.code} has no measurements in the last hour, trying the next station")
                continue
            return latest_measurements
        return None

    def list_snow_stations(self) -> List[SLFStation]:
        """List the IMIS stations that have daily snow, and the manual sites."""
        station_code_to_daily_snow = self._read_all_imis_stations_daily_snow()
        snow_stations = []
        for station in self._load_stations():
            if station.code in station_code_to_daily_snow:
                snow_stations.append(station)
        manual_sites = self._load_manual_sites()
        snow_stations.extend(manual_sites)
        return snow_stations

    def read_snow_depth(self, station: SLFStation) -> Optional[SnowDepth]:
        """
        Read the current snow depth of a station, or None when it has none.

        An IMIS station's counts when it is at most SNOW_DEPTH_MAX_AGE old, and a manual site's when
        it is from today or yesterday.
        """
        if station.is_manual_site:
            return self._read_manual_site_snow_depth(station)
        return self._read_imis_snow_depth(station)

    def read_new_snow(self, station: SLFStation) -> Optional[NewSnow]:
        """
        Read the new snow of a station's newest day, or None when it has none from today or yesterday.

        The new snow of a manual site is measured by an observer, the one of an IMIS station calculated by
        SLF from the snow depth.
        """
        if station.is_manual_site:
            all_days = self._read_manual_site_daily_snow(station)
        else:
            station_code_to_daily_snow = self._read_all_imis_stations_daily_snow()
            all_days = station_code_to_daily_snow.get(station.code, [])
        today = datetime.now(timezone.utc).date()
        return _calculate_new_snow(station, all_days, today)
