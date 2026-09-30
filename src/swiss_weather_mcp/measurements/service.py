import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, NamedTuple, Optional

from . import parameters
from ..errors import CannotAnswerError
from ..formatting import SWISS_TZ, find_compass_point, format_swiss_time
from ..locations import LocationPoint, LocationFinder
from .source import MeasurementSource, Station, StationMeasurements, calculate_distance_m

logger = logging.getLogger(__name__)

METRES_PER_KILOMETRE = 1000

# The usual period for a pressure tendency
CHANGE_PERIOD = timedelta(hours=3)
SUNSHINE_PERIOD = timedelta(hours=1)
# A station publishes a row every 10 minutes
MEASUREMENTS_PER_HOUR = 6

# The field each measured value fills in the answer
MEASURED_FIELDS = (
    ("temperature_c", parameters.TEMPERATURE),
    ("humidity_percent", parameters.HUMIDITY),
    ("dew_point_c", parameters.DEW_POINT),
    ("rain_last_10_minutes_mm", parameters.PRECIPITATION),
    ("sunshine_last_10_minutes_min", parameters.SUNSHINE),
    ("wind_speed_kmh", parameters.WIND_SPEED),
    ("gusts_kmh", parameters.WIND_GUSTS),
    ("wind_direction_degrees", parameters.WIND_DIRECTION),
    ("pressure_sea_level_hpa", parameters.PRESSURE_SEA_LEVEL),
)


class Extreme(NamedTuple):
    """The measured value an extreme ranks the stations by."""
    parameter: str
    field: str  # the field the value fills in the answer
    highest_first: bool


ExtremeName = Literal["warmest", "coldest", "windiest", "wettest", "sunniest"]
EXTREMES: Dict[ExtremeName, Extreme] = {
    "warmest": Extreme(parameters.TEMPERATURE, "temperature_c", highest_first=True),
    "coldest": Extreme(parameters.TEMPERATURE, "temperature_c", highest_first=False),
    "windiest": Extreme(parameters.WIND_GUSTS, "gusts_kmh", highest_first=True),
    "wettest": Extreme(parameters.PRECIPITATION, "rain_last_10_minutes_mm", highest_first=True),
    "sunniest": Extreme(parameters.SUNSHINE, "sunshine_last_10_minutes_min", highest_first=True),
}
# How many stations a ranking lists
STATION_COUNT = 5
# A station is sunny when the sun shone for at least half of the last 10 minutes
SUNNY_MIN_SUNSHINE_MIN = 5.0
# The smallest amount a rain gauge reports
RAINY_MIN_RAIN_MM = 0.1


class StationValue(NamedTuple):
    """One measured value of one station."""
    station: Station
    value: float


def _describe_height_difference(difference_m: int) -> str:
    """Say how much higher or lower the station is, such as "801 m lower"."""
    if difference_m > 0:
        return f"{difference_m} m higher"
    if difference_m < 0:
        return f"{-difference_m} m lower"
    return "at the same height"


def _build_nearest_station_sentence(point: LocationPoint, measurements: StationMeasurements,
                                    distance_km: float, difference_m: int) -> str:
    """Build the sentence that says which station measured the values, such as "Glarus (517 m) is ..."."""
    measured_at = measurements.measured_at.astimezone(SWISS_TZ)
    return (
        f"{measurements.station.display_name} is the nearest MeteoSwiss station to {point.display_name}, "
        f"{distance_km} km away and {_describe_height_difference(difference_m)}. "
        f"It measured the following values at {measured_at:%H:%M}."
    )


def _find_measurements_at(all_measurements: List[StationMeasurements],
                          measured_at: datetime) -> Optional[StationMeasurements]:
    """Find the measurements of a time, or None when the station published none for it."""
    for measurements in all_measurements:
        if measurements.measured_at == measured_at:
            return measurements
    return None


def _calculate_change(all_measurements: List[StationMeasurements], parameter: str) -> Optional[float]:
    """Return how much a value changed over the last 3 hours, or None when either value is missing."""
    latest_measurements = all_measurements[-1]
    earlier_measurements = _find_measurements_at(all_measurements, latest_measurements.measured_at - CHANGE_PERIOD)
    if earlier_measurements is None:
        return None
    latest_value = latest_measurements.values[parameter]
    earlier_value = earlier_measurements.values[parameter]
    if latest_value is None or earlier_value is None:
        return None
    return round(latest_value - earlier_value, 1)


def _sum_sunshine_last_hour(all_measurements: List[StationMeasurements]) -> Optional[float]:
    """Return the minutes of sunshine in the last hour, or None when one of its 10-minute values is missing."""
    latest_measurements = all_measurements[-1]
    period_start = latest_measurements.measured_at - SUNSHINE_PERIOD

    sunshine_values: List[float] = []
    for measurements in all_measurements:
        if measurements.measured_at > period_start:
            sunshine_min = measurements.values[parameters.SUNSHINE]
            if sunshine_min is None:
                return None
            sunshine_values.append(sunshine_min)

    if len(sunshine_values) < MEASUREMENTS_PER_HOUR:
        return None
    return sum(sunshine_values)


def _find_station_values(all_measurements: List[StationMeasurements], parameter: str) -> List[StationValue]:
    """Find the stations that have a value for a parameter, so a station without one is never counted."""
    station_values = []
    for measurements in all_measurements:
        value = measurements.values[parameter]
        if value is not None:
            station_values.append(StationValue(measurements.station, value))
    return station_values


def _find_station_values_from(station_values: List[StationValue], minimum: float) -> List[StationValue]:
    """Find the stations whose value is at least a minimum."""
    matching_values = []
    for station_value in station_values:
        if station_value.value >= minimum:
            matching_values.append(station_value)
    return matching_values


def _build_station_rows(station_values: List[StationValue], extreme: Extreme) -> List[Dict[str, Any]]:
    """Build the answer rows of the first 5 stations in the order of an extreme."""
    ranked_values = sorted(
        station_values, key=lambda station_value: station_value.value, reverse=extreme.highest_first
    )
    rows = []
    for station_value in ranked_values[:STATION_COUNT]:
        rows.append({
            "station": station_value.station.display_name,
            "canton": station_value.station.canton,
            extreme.field: station_value.value,
        })
    return rows


def _count_stations_by_canton(station_values: List[StationValue]) -> Dict[str, int]:
    """Count the stations of each canton."""
    counts: Dict[str, int] = {}
    for station_value in station_values:
        canton = station_value.station.canton
        counts[canton] = counts.get(canton, 0) + 1
    return counts


def _build_canton_rows(station_values: List[StationValue], matching_values: List[StationValue],
                       label: str) -> List[Dict[str, Any]]:
    """
    Build one answer row for each canton with a matching station, such as a sunny one: how many
    of the canton's measuring stations match. The canton with the highest share comes first.
    """
    measuring_counts = _count_stations_by_canton(station_values)
    matching_counts = _count_stations_by_canton(matching_values)
    rows: List[Dict[str, Any]] = []
    for canton, matching_count in matching_counts.items():
        measuring_count = measuring_counts[canton]
        rows.append({
            "canton": canton,
            f"{label}_stations": matching_count,
            "measuring_stations": measuring_count,
            f"{label}_percent": round(100 * matching_count / measuring_count),
        })
    # Among equal shares the canton with more stations comes first, because its share is better supported
    rows.sort(key=lambda row: (-row[f"{label}_percent"], -row["measuring_stations"], row["canton"]))
    return rows


class MeasurementService:
    """Answer what the weather is now, at a location and across all stations, from the station measurements."""

    def __init__(self, location_finder: LocationFinder, measurement_source: MeasurementSource):
        self.location_finder = location_finder
        self.measurement_source = measurement_source

    async def _find_point(self, location: str) -> LocationPoint:
        """Find the location point for a location in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.location_finder.find_point, location)

    async def _find_nearest_station_measurements(self, point: LocationPoint) -> List[StationMeasurements]:
        """Find the nearest station's measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.measurement_source.find_nearest_station_measurements, point)

    async def _read_all_stations_latest_measurements(self) -> List[StationMeasurements]:
        """Read every station's latest measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.measurement_source.read_all_stations_latest_measurements)

    async def read_current_conditions(self, location: str) -> Dict[str, Any]:
        """
        Read the latest measurements of the station nearest to a location.

        Return the resolved location, the station with its distance and its height above the
        location (negative when lower), the time of the measurements, and the measured values, None
        where the station does not measure them. The temperature and pressure changes over the last
        3 hours and the sunshine of the last hour are None when the station has not published all
        the measurements they need. Raise CannotAnswerError for an unknown location or when no
        station publishes a temperature.
        """
        point = await self._find_point(location)
        all_measurements = await self._find_nearest_station_measurements(point)
        latest_measurements = all_measurements[-1]
        station = latest_measurements.station
        distance_km = round(calculate_distance_m(point, station) / METRES_PER_KILOMETRE, 1)
        difference_m = round(station.altitude_m - point.altitude_m)

        answer: Dict[str, Any] = {
            "nearest_station": _build_nearest_station_sentence(point, latest_measurements, distance_km, difference_m),
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "station": station.display_name,
            "station_distance_km": distance_km,
            "station_altitude_difference_m": difference_m,
            "measured_at": format_swiss_time(latest_measurements.measured_at),
        }
        for field, parameter in MEASURED_FIELDS:
            answer[field] = latest_measurements.values[parameter]

        wind_direction_degrees = latest_measurements.values[parameters.WIND_DIRECTION]
        if wind_direction_degrees is None:
            answer["compass_point"] = None
        else:
            answer["compass_point"] = find_compass_point(wind_direction_degrees)

        answer["temperature_change_last_3_hours_c"] = _calculate_change(all_measurements, parameters.TEMPERATURE)
        answer["pressure_change_last_3_hours_hpa"] = _calculate_change(all_measurements, parameters.PRESSURE_SEA_LEVEL)
        answer["sunshine_last_hour_min"] = _sum_sunshine_last_hour(all_measurements)
        return answer

    async def read_current_extremes(self, extreme: str) -> Dict[str, Any]:
        """
        Read where in Switzerland a measured value is at its extreme now, from every station's
        latest measurements.

        extreme is one of EXTREMES. Return the time of the measurements and, for the warmest, the
        coldest and the windiest, the first 5 stations. For the sunniest and the wettest return
        how many of the measuring stations are sunny or rainy, in all and for each canton with at
        least one, and for the wettest also the first 5 rainy stations. A station without a value
        is left out everywhere. Raise CannotAnswerError for an unknown extreme or when MeteoSwiss
        publishes no measurements.
        """
        if extreme not in EXTREMES:
            raise CannotAnswerError(f"'{extreme}' is not a known extreme, use one of: {', '.join(EXTREMES)}.")

        all_measurements = await self._read_all_stations_latest_measurements()
        if not all_measurements:
            raise CannotAnswerError("MeteoSwiss currently publishes no measurements, try again later.")

        ranked_by = EXTREMES[extreme]
        station_values = _find_station_values(all_measurements, ranked_by.parameter)
        answer: Dict[str, Any] = {
            "extreme": extreme,
            # Every row of the all stations file has the same time
            "measured_at": format_swiss_time(all_measurements[0].measured_at),
        }
        if extreme == "sunniest":
            sunny_values = _find_station_values_from(station_values, SUNNY_MIN_SUNSHINE_MIN)
            answer["sunny_stations"] = len(sunny_values)
            answer["measuring_stations"] = len(station_values)
            answer["cantons"] = _build_canton_rows(station_values, sunny_values, "sunny")
        elif extreme == "wettest":
            rainy_values = _find_station_values_from(station_values, RAINY_MIN_RAIN_MM)
            answer["rainy_stations"] = len(rainy_values)
            answer["measuring_stations"] = len(station_values)
            answer["cantons"] = _build_canton_rows(station_values, rainy_values, "rainy")
            answer["stations"] = _build_station_rows(rainy_values, ranked_by)
        else:
            answer["stations"] = _build_station_rows(station_values, ranked_by)
        return answer
