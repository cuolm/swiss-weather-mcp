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
from .sky import ARCSECONDS_PER_DEGREE, find_altitude_deg, find_arguments, find_rise_and_set, find_swiss_day, sum_series

# Equation (5), the obliquity of the ecliptic, and the nutation in longitude after it, in arcseconds
OBLIQUITY_AT_1900_ARCSEC = 84428
OBLIQUITY_PER_CENTURY_ARCSEC = -47
OBLIQUITY_NUTATION_ARCSEC = 9
LONGITUDE_NUTATION_ARCSEC = -17

# Sunrise and sunset happen when the geometric zenith distance of the centre of the Sun is 90.8333°,
# and civil twilight begins and ends at 96°
SUNRISE_ZENITH_DISTANCE_DEG = 90.8333
CIVIL_TWILIGHT_ZENITH_DISTANCE_DEG = 96
ZENITH_ALTITUDE_DEG = 90


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


def find_sun_position(moment: datetime) -> SunPosition:
    """
    Find the right ascension and declination of the Sun at a timezone aware moment.

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
    arguments, centuries_since_1900 = find_arguments(moment)
    sun_plon = sum_series(SUN_PLON, SUN_ARGUMENTS, arguments, centuries_since_1900)
    moon_node = arguments["moon_node"]
    nutation_arcsec = LONGITUDE_NUTATION_ARCSEC * math.sin(moon_node)
    longitude = arguments["sun_mean_longitude"] + math.radians((sun_plon + nutation_arcsec) / ARCSECONDS_PER_DEGREE)
    obliquity_arcsec = (
        OBLIQUITY_AT_1900_ARCSEC
        + OBLIQUITY_PER_CENTURY_ARCSEC * centuries_since_1900
        + OBLIQUITY_NUTATION_ARCSEC * math.cos(moon_node)
    )
    obliquity = math.radians(obliquity_arcsec / ARCSECONDS_PER_DEGREE)
    right_ascension = math.atan2(math.sin(longitude) * math.cos(obliquity), math.cos(longitude))
    declination = math.asin(math.sin(longitude) * math.sin(obliquity))
    return SunPosition(
        right_ascension_deg=math.degrees(right_ascension) % 360,
        declination_deg=math.degrees(declination),
    )


def find_sun_day(day: date, latitude_deg: float, longitude_deg: float) -> SunDay:
    """Find the begin of civil twilight, sunrise, sunset and the end of civil twilight on a Swiss calendar day."""
    day_start, day_end = find_swiss_day(day)

    def find_altitude(moment: datetime) -> float:
        position = find_sun_position(moment)
        return find_altitude_deg(
            moment, position.right_ascension_deg, position.declination_deg, latitude_deg, longitude_deg
        )

    def find_height_above_sunrise(moment: datetime) -> float:
        return find_altitude(moment) - (ZENITH_ALTITUDE_DEG - SUNRISE_ZENITH_DISTANCE_DEG)

    def find_height_above_civil_twilight(moment: datetime) -> float:
        return find_altitude(moment) - (ZENITH_ALTITUDE_DEG - CIVIL_TWILIGHT_ZENITH_DISTANCE_DEG)

    sunrise, sunset = find_rise_and_set(find_height_above_sunrise, day_start, day_end)
    civil_twilight_begin, civil_twilight_end = find_rise_and_set(find_height_above_civil_twilight, day_start, day_end)
    return SunDay(
        civil_twilight_begin=civil_twilight_begin,
        sunrise=sunrise,
        sunset=sunset,
        civil_twilight_end=civil_twilight_end,
    )
