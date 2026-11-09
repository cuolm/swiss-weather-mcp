"""
Read the latest measurements of the IMIS weather stations from the SLF measurement API.

What SLF publishes:
- The measurements of a station, such as imis/station/DAV2/measurements, with
  period_in_days=1 the rows of the last 24 hours. A row every 30 minutes, at :00 and
  :30 UTC, holds the means and maxima of the 30 minutes up to its time. SLF adds a row
  about 10 minutes after its time. A value a station does not measure is null.
- A station list, imis/stations, with one object per station: its name, altitude,
  position in WGS84 latitude and longitude, canton and type. About 200 stations, most of
  them between 1300 and 3300 m, in Switzerland and Liechtenstein.
- Both answers are JSON. A station code, such as DAV2, links the station list to the
  station's measurements.

    imis/station/DAV2/measurements?period_in_days=1  (measurements of Bärentälli, 15 fields, the server reads 5 values)
    ┌────────────────────────────────────────────────────────────────────┐
    │ [{"station_code": "DAV2", "measure_date": "2026-11-09T12:00:00Z",  │  station, time in UTC
    │   "TA_30MIN_MEAN": -0.338, "RH_30MIN_MEAN": 79.5,                  │  -0.3 °C, 79.5 %
    │   "VW_30MIN_MEAN": 0.652, "VW_30MIN_MAX": 2.933,                   │  wind and 5-second peak in m/s
    │   "DW_30MIN_MEAN": 58.52, "HS": 1.0, ...},                         │  direction, snow depth
    │  ...]                                                              │  one row every 30 minutes
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
   older than 5 minutes. Its last row holds the latest measurements.
4. Take the next station when the latest measurements have no temperature or are older
   than 1 hour, at most 3 stations. A station without a temperature would answer without
   the main value, and a station that stopped publishing would answer with old values.

    <cache dir>/slf/
    ├── imis_stations.json             (station list, downloaded again when older than 7 days)
    └── imis_dav2_measurements.json    (measurements per station, downloaded again after 5 minutes)
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

from ..distance import calculate_weighted_distance_m
from ..opendata import ensure_recent_file
from ..places import SwisstopoPlace

logger = logging.getLogger(__name__)

STATION_LIST_URL = "https://measurement-api.slf.ch/public/api/imis/stations"
STATION_MEASUREMENTS_URL = "https://measurement-api.slf.ch/public/api/imis/station/{code}/measurements?period_in_days=1"

# SLF says nothing on how often the list changes, so it is kept as long as the MeteoSwiss station table
STATION_LIST_MAX_AGE = timedelta(days=7)
# SLF's own server keeps an answer about 5 minutes
STATION_MEASUREMENTS_MAX_AGE = timedelta(minutes=5)
# Older latest measurements mean the station stopped publishing
LATEST_MEASUREMENTS_MAX_AGE = timedelta(hours=1)
MAX_STATIONS_TRIED = 3

# The SLF description of each value, from https://measurement-api.slf.ch/openapi.json
TEMPERATURE = "TA_30MIN_MEAN"     # air temperature, mean over last 30 minute, °C
HUMIDITY = "RH_30MIN_MEAN"        # relative humidity, mean over last 30 minutes, %
WIND_SPEED = "VW_30MIN_MEAN"      # wind speed, vectorial mean over last 30 minutes, m/s
WIND_GUSTS = "VW_30MIN_MAX"       # wind speed, max 5sec measurement within last 30 minutes, m/s
WIND_DIRECTION = "DW_30MIN_MEAN"  # wind direction, vectorial mean over last 30 minutes, °

# The swisstopo approximate formulas from WGS84 to LV95, section 1 of "Approximate formulas for the
# transformation between Swiss projection coordinates and WGS84" (December 2016),
# https://www.swisstopo.admin.ch/dam/en/sd-web/KLRCX9XIdXDu/ch1903wgs84-EN.pdf
# They are better than 1 metre everywhere in Switzerland.
ARCSECONDS_PER_DEGREE = 3600
BERN_LATITUDE_ARCSECONDS = 169028.66
BERN_LONGITUDE_ARCSECONDS = 26782.5
# The formulas take the angles relative to Bern in units of 10000"
ARCSECONDS_PER_10000_ARCSECONDS = 10000


class SLFStation(NamedTuple):
    """An IMIS station as the SLF station list describes it."""
    code: str
    name: str
    altitude_m: float
    east_m: float   # LV95, the Swiss grid in metres, turned from the WGS84 position SLF publishes
    north_m: float  # LV95

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
    wind_speed_m_per_s: Optional[float]
    strongest_gust_m_per_s: Optional[float]  # the highest 5-second wind
    wind_direction_degrees: Optional[float]


def _calculate_offset_from_bern(angle_deg: float, bern_angle_arcseconds: float) -> float:
    """Return how far an angle lies from Bern's, in the unit of the formulas, 10000"."""
    return (angle_deg * ARCSECONDS_PER_DEGREE - bern_angle_arcseconds) / ARCSECONDS_PER_10000_ARCSECONDS


def _calculate_east_m(latitude_deg: float, longitude_deg: float) -> float:
    """Return the LV95 east coordinate of a WGS84 position, by the swisstopo approximate formulas."""
    latitude = _calculate_offset_from_bern(latitude_deg, BERN_LATITUDE_ARCSECONDS)
    longitude = _calculate_offset_from_bern(longitude_deg, BERN_LONGITUDE_ARCSECONDS)
    return (
        2600072.37
        + 211455.93 * longitude
        - 10938.51 * longitude * latitude
        - 0.36 * longitude * latitude**2
        - 44.54 * longitude**3
    )


def _calculate_north_m(latitude_deg: float, longitude_deg: float) -> float:
    """Return the LV95 north coordinate of a WGS84 position, by the swisstopo approximate formulas."""
    latitude = _calculate_offset_from_bern(latitude_deg, BERN_LATITUDE_ARCSECONDS)
    longitude = _calculate_offset_from_bern(longitude_deg, BERN_LONGITUDE_ARCSECONDS)
    return (
        1200147.07
        + 308807.95 * latitude
        + 3745.25 * longitude**2
        + 76.63 * latitude**2
        - 194.56 * longitude**2 * latitude
        + 119.79 * latitude**3
    )


def _parse_station(station_object: Dict[str, Any]) -> SLFStation:
    """Turn one object of the station list into a station on LV95."""
    latitude_deg = station_object["lat"]
    longitude_deg = station_object["lon"]
    return SLFStation(
        code=station_object["code"],
        name=station_object["label"],
        altitude_m=station_object["elevation"],
        east_m=_calculate_east_m(latitude_deg, longitude_deg),
        north_m=_calculate_north_m(latitude_deg, longitude_deg),
    )


def _parse_station_measurements(station: SLFStation, row: Dict[str, Any]) -> SLFStationMeasurements:
    """Read the values the server uses from one row of a station's measurements."""
    # Such as "2026-11-09T12:00:00Z", which fromisoformat reads as UTC
    measured_at = datetime.fromisoformat(row["measure_date"])
    return SLFStationMeasurements(
        station=station,
        measured_at=measured_at,
        temperature_c=row[TEMPERATURE],
        humidity_percent=row[HUMIDITY],
        wind_speed_m_per_s=row[WIND_SPEED],
        strongest_gust_m_per_s=row[WIND_GUSTS],
        wind_direction_degrees=row[WIND_DIRECTION],
    )


class SLFMeasurementSource:
    """Read the latest IMIS measurements and find a station that suits a place better than the MeteoSwiss one."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "slf"
        self._stations: List[SLFStation] = []

    def _load_stations(self) -> List[SLFStation]:
        """Read the station list into memory once per process."""
        if self._stations:
            return self._stations

        station_list_file = self.cache_dir / "imis_stations.json"
        station_list = ensure_recent_file(STATION_LIST_URL, station_list_file, STATION_LIST_MAX_AGE)
        with open(station_list, encoding="utf-8") as file:
            station_objects = json.load(file)
        stations = []
        for station_object in station_objects:
            station = _parse_station(station_object)
            stations.append(station)
        # Published only once complete, so a parallel request never sees part of the list
        self._stations = stations
        logger.info(f"Loaded {len(stations)} SLF stations")
        return stations

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

    def _rank_closer_slf_stations(
        self, place: SwisstopoPlace, meteoswiss_station_weighted_distance_m: float
    ) -> List[SLFStation]:
        """Rank the stations with a smaller weighted distance to a place than the MeteoSwiss station's, the best first."""
        closer_slf_stations = []
        for station in self._load_stations():
            if calculate_weighted_distance_m(place, station) < meteoswiss_station_weighted_distance_m:
                closer_slf_stations.append(station)
        return sorted(closer_slf_stations, key=lambda station: calculate_weighted_distance_m(place, station))

    def find_closer_slf_station_measurements(
        self, place: SwisstopoPlace, meteoswiss_station_weighted_distance_m: float
    ) -> Optional[SLFStationMeasurements]:
        """
        Find the latest measurements of an SLF station that suits a place better than the MeteoSwiss station.

        meteoswiss_station_weighted_distance_m is the MeteoSwiss station's distance to the place plus
        METRES_PER_HEIGHT_METRE times its height gap, from calculate_weighted_distance_m. Only stations
        with a smaller one are tried, the best first, and the first one that published a temperature in
        the last hour is taken. Return None when there is no such station among the first MAX_STATIONS_TRIED.
        """
        ranked_slf_stations = self._rank_closer_slf_stations(place, meteoswiss_station_weighted_distance_m)
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
