"""Write times, wind directions, distances and height differences the way the tools answer with them."""
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from .distance import METRES_PER_KILOMETRE, GridPosition, calculate_distance_m
from .places import SwisstopoPlace

SWISS_TZ = ZoneInfo("Europe/Zurich")

# Compass points the wind direction in degrees is reported as, clockwise from north
COMPASS_POINTS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
COMPASS_SECTOR_DEGREES = 360 / len(COMPASS_POINTS)


def format_swiss_time(moment: datetime) -> str:
    """Format a moment as ISO 8601 Swiss local time with its UTC offset, such as 2026-09-23T14:00+02:00."""
    return moment.astimezone(SWISS_TZ).isoformat(timespec="minutes")


def find_compass_point(degrees: float) -> str:
    """Find the compass point a bearing falls in, such as "SW" for 217 degrees."""
    # Rounding picks the nearest point, and the modulo turns a bearing just short of 360 back into N
    sector = round(degrees / COMPASS_SECTOR_DEGREES) % len(COMPASS_POINTS)
    return COMPASS_POINTS[sector]


def describe_height_difference(difference_m: int) -> str:
    """Say how much higher or lower something is, such as "801 m lower"."""
    if difference_m > 0:
        return f"{difference_m} m higher"
    if difference_m < 0:
        return f"{-difference_m} m lower"
    return "at the same height"


def describe_distance_and_height(place: SwisstopoPlace, position: GridPosition) -> str:
    """Say how far away and how much higher or lower a station is, such as "0.5 km away and 30 m higher"."""
    distance_km = round(calculate_distance_m(place, position) / METRES_PER_KILOMETRE, 1)
    difference_m = round(position.altitude_m - place.altitude_m)
    return f"{distance_km} km away and {describe_height_difference(difference_m)}"


def round_to_tenth(value: Optional[float]) -> Optional[float]:
    """Round a value to a tenth, as MeteoSwiss publishes them, or return None for None."""
    if value is None:
        return None
    return round(value, 1)
