import asyncio
import logging
from typing import Any, Dict

from . import parameters
from ..formatting import find_compass_point, format_swiss_time
from ..locations import ForecastPoint, LocationFinder
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

        answer: Dict[str, Any] = {
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "station": station.display_name,
            "station_distance_km": round(find_distance_m(point, station) / METRES_PER_KILOMETRE, 1),
            "station_altitude_difference_m": round(station.altitude_m - point.altitude_m),
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
