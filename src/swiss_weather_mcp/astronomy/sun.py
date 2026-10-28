"""
Calculate when the Sun rises and sets at a place, and when civil twilight begins and ends.

Sources, linked in the package docstring:
- The position of the Sun: Van Flandern and Pulkkinen (1979), sections IV and V, equations (5)
  and (6) and Table 4 (see series.py)
- Sidereal time, hour angle and altitude: see sky.py
- Sunrise, sunset and civil twilight: USNO, "Rise, Set, and Twilight Definitions"

Like USNO, the times are for a level horizon at sea level.
"""
import math
from datetime import date, datetime
from typing import NamedTuple, Optional

from .series import SUN_ARGUMENTS, SUN_PLON
from .sky import (
    ARCSECONDS_PER_DEGREE, calculate_altitude_deg, calculate_centuries_since_1900, calculate_ecliptic_longitude_deg,
    calculate_fundamental_arguments_rad, calculate_trigonometric_series, find_crossings, list_minutes_of_day,
)

# Equation (5), the obliquity of the ecliptic (the tilt of the Earth's axis), and the nutation in
# longitude after it, in arcseconds
OBLIQUITY_AT_1900_ARCSEC = 84428
OBLIQUITY_PER_CENTURY_ARCSEC = -47
OBLIQUITY_NUTATION_ARCSEC = 9
LONGITUDE_NUTATION_ARCSEC = -17

# Sunrise and sunset happen when the geometric zenith distance of the centre of the Sun is 90.8333°,
# and civil twilight begins and ends at 96°, so when its altitude is -0.8333° and -6°
SUNRISE_ALTITUDE_DEG = -0.8333
CIVIL_TWILIGHT_ALTITUDE_DEG = -6


class SunPosition(NamedTuple):
    """Where the Sun is seen from the centre of the Earth, on the equator and equinox of date."""
    right_ascension_deg: float
    declination_deg: float


class SunDay(NamedTuple):
    """The Sun on one Swiss calendar day at one place; each moment is None when it does not happen."""
    civil_twilight_begin: Optional[datetime]
    sunrise: Optional[datetime]
    sunset: Optional[datetime]
    civil_twilight_end: Optional[datetime]


def calculate_sun_position(moment: datetime) -> SunPosition:
    """
    Calculate the right ascension and declination of the Sun at a timezone aware moment.

    By section IV of Van Flandern and Pulkkinen, "Low-precision formulae for planetary positions"
    (1979), the ecliptic longitude of the Sun is λ = L_S + PLON. Section V turns it into equatorial
    coordinates with the obliquity of the ecliptic, equation (5),

        ε = 84428" - 47" T + 9" cos(Ω_M)

    "together with a correction for nutation in longitude for each body involved, given by
    δλ = -17" sin(Ω_M)", by equation (6):

        cos δ cos α = cos β cos(λ + δλ)
        cos δ sin α = cos β sin(λ + δλ) cos ε - sin β sin ε
        sin δ = cos β sin(λ + δλ) sin ε + sin β cos ε

    α and δ are the right ascension and declination. The ecliptic latitude β of the Sun is 0 in Table 4.
    """
    fundamental_arguments_rad = calculate_fundamental_arguments_rad(moment)
    centuries_since_1900 = calculate_centuries_since_1900(moment)
    sun_longitude_correction_arcsec = calculate_trigonometric_series(
        SUN_PLON, SUN_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900
    )

    moon_node_rad = fundamental_arguments_rad["moon_node"]
    nutation_arcsec = LONGITUDE_NUTATION_ARCSEC * math.sin(moon_node_rad)
    # λ + δλ of equation (6)
    longitude_deg = calculate_ecliptic_longitude_deg(
        fundamental_arguments_rad["sun_mean_longitude"], sun_longitude_correction_arcsec + nutation_arcsec
    )
    longitude_rad = math.radians(longitude_deg)

    obliquity_arcsec = (
        OBLIQUITY_AT_1900_ARCSEC
        + OBLIQUITY_PER_CENTURY_ARCSEC * centuries_since_1900
        + OBLIQUITY_NUTATION_ARCSEC * math.cos(moon_node_rad)
    )
    obliquity_rad = math.radians(obliquity_arcsec / ARCSECONDS_PER_DEGREE)

    right_ascension_rad = math.atan2(math.sin(longitude_rad) * math.cos(obliquity_rad), math.cos(longitude_rad))
    declination_rad = math.asin(math.sin(longitude_rad) * math.sin(obliquity_rad))
    return SunPosition(
        right_ascension_deg=math.degrees(right_ascension_rad) % 360,
        declination_deg=math.degrees(declination_rad),
    )


def _calculate_sun_altitude_deg(moment: datetime, latitude_deg: float, longitude_deg: float) -> float:
    """Calculate the altitude of the centre of the Sun at a moment, seen from a place, in degrees."""
    position = calculate_sun_position(moment)
    return calculate_altitude_deg(
        moment, position.right_ascension_deg, position.declination_deg, latitude_deg, longitude_deg
    )


def calculate_sun_day(day: date, latitude_deg: float, longitude_deg: float) -> SunDay:
    """Calculate the begin of civil twilight, sunrise, sunset and the end of civil twilight on a Swiss calendar day."""
    minute_moments = list_minutes_of_day(day)
    altitudes_deg = [_calculate_sun_altitude_deg(moment, latitude_deg, longitude_deg) for moment in minute_moments]

    sunrise, sunset = find_crossings(minute_moments, altitudes_deg, SUNRISE_ALTITUDE_DEG)
    civil_twilight_begin, civil_twilight_end = find_crossings(
        minute_moments, altitudes_deg, CIVIL_TWILIGHT_ALTITUDE_DEG
    )

    return SunDay(
        civil_twilight_begin=civil_twilight_begin,
        sunrise=sunrise,
        sunset=sunset,
        civil_twilight_end=civil_twilight_end,
    )
