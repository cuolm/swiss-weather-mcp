import asyncio
import logging
from typing import Any, Dict

from . import parameters
from ..formatting import find_compass_point, format_swiss_time
from ..locations import ForecastPoint, LocationFinder
from ..opendata import SWISS_TZ
from .source import MeasurementSource, StationMeasurements, find_distance_m

logger = logging.getLogger(__name__)

METRES_PER_KILOMETRE = 1000

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


def _build_nearest_station_sentence(point: ForecastPoint, measurements: StationMeasurements,
                                    distance_km: float, difference_m: int) -> str:
    """Build the sentence that says which station measured the values, such as "Glarus (517 m) is ..."."""
    measured_at = measurements.measured_at.astimezone(SWISS_TZ)
    return (
        f"{measurements.station.display_name} is the nearest MeteoSwiss station to {point.display_name}, "
        f"{distance_km} km away and {_describe_height_difference(difference_m)}. "
        f"It measured the following values at {measured_at:%H:%M}."
    )


class MeasurementService:
    """Answer what the weather is now at a location, from the nearest station's measurements."""

    def __init__(self, location_finder: LocationFinder, measurement_source: MeasurementSource):
        self.location_finder = location_finder
        self.measurement_source = measurement_source

    async def _find_point(self, location: str) -> ForecastPoint:
        """Find the forecast point for a location in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.location_finder.find_point, location)

    async def _find_nearest_measurements(self, point: ForecastPoint) -> StationMeasurements:
        """Find the nearest station's measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.measurement_source.find_nearest_measurements, point)

    async def read_current_conditions(self, location: str) -> Dict[str, Any]:
        """
        Read the latest measurements of the station nearest to a location.

        Parameters:
            location (str): Location name or postal code.

        Returns:
            Dict[str, Any]: The resolved location, the station with its distance and its height
                above the location (negative when lower), the time of the measurements, and the
                measured values, None where the station does not measure them.
        """
        point = await self._find_point(location)
        measurements = await self._find_nearest_measurements(point)
        station = measurements.station
        distance_km = round(find_distance_m(point, station) / METRES_PER_KILOMETRE, 1)
        difference_m = round(station.altitude_m - point.altitude_m)

        answer: Dict[str, Any] = {
            "nearest_station": _build_nearest_station_sentence(point, measurements, distance_km, difference_m),
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "station": station.display_name,
            "station_distance_km": distance_km,
            "station_altitude_difference_m": difference_m,
            "measured_at": format_swiss_time(measurements.measured_at),
        }
        for field, parameter in MEASURED_FIELDS:
            answer[field] = measurements.values[parameter]

        wind_direction_degrees = answer["wind_direction_degrees"]
        if wind_direction_degrees is None:
            answer["compass_point"] = None
        else:
            answer["compass_point"] = find_compass_point(wind_direction_degrees)
        return answer
