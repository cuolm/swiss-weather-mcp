import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from . import parameters
from ..formatting import SWISS_TZ, find_compass_point, format_swiss_time
from ..locations import LocationPoint, LocationFinder
from .source import MeasurementSource, StationMeasurements, calculate_distance_m

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

    sunshine_values = []
    for measurements in all_measurements:
        if measurements.measured_at > period_start:
            sunshine_values.append(measurements.values[parameters.SUNSHINE])

    if len(sunshine_values) < MEASUREMENTS_PER_HOUR or None in sunshine_values:
        return None
    return sum(sunshine_values)


class MeasurementService:
    """Answer what the weather is now at a location, from the nearest station's measurements."""

    def __init__(self, location_finder: LocationFinder, measurement_source: MeasurementSource):
        self.location_finder = location_finder
        self.measurement_source = measurement_source

    async def _find_point(self, location: str) -> LocationPoint:
        """Find the location point for a location in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.location_finder.find_point, location)

    async def _find_nearest_measurements(self, point: LocationPoint) -> List[StationMeasurements]:
        """Find the nearest station's measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.measurement_source.find_nearest_measurements, point)

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
        all_measurements = await self._find_nearest_measurements(point)
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
