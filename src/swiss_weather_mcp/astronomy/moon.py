"""
Calculate when the Moon rises and sets at a place, its phase, and how much of it is lit.

Sources, linked in the package docstring:
- The positions of the Moon and the Sun: Van Flandern and Pulkkinen (1979), equation (17) and
  Table 4 (see series.py)
- Sidereal time, hour angle and altitude: see sky.py
- When the Moon rises and sets: USNO, "Rise, Set, and Twilight Definitions"
- The phases: USNO, "Phases of the Moon and Percent of the Moon Illuminated", and the glossary of
  The Astronomical Almanac
- The lit fraction and the radius of the Moon: Explanatory Supplement to the Astronomical Almanac
  (1992), equations 7.12-32 and 7.3-10

Like USNO, the times are for a level horizon at sea level.
"""
import math
from datetime import date, datetime, time
from typing import NamedTuple, Optional, Tuple

from ..formatting import SWISS_TZ
from .series import MOON_ARGUMENTS, MOON_BETA, MOON_PLON, MOON_U, MOON_V, MOON_W, SUN_ARGUMENTS, SUN_PLON, SUN_RP
from .sky import (
    ARCSECONDS_PER_DEGREE, calculate_altitude_deg, calculate_arguments, find_rise_and_set, find_sign_change,
    find_swiss_day, sum_series,
)

# The scaling factor Δ of the Moon, section V, which gives its distance in Earth radii from U
MOON_DISTANCE_SCALE_EARTH_RADII = 60.40974
# 1 astronomical unit is 23454.8 Earth radii (section IV)
EARTH_RADII_PER_ASTRONOMICAL_UNIT = 23454.8

# The Moon rises or sets when its centre is 90.5666° + its apparent radius - its horizontal
# parallax from the zenith; 0.5666° (34') is the refraction at the horizon
REFRACTION_AT_HORIZON_DEG = 0.5666
# The radius of the Moon in equation 7.3-10, and the IAU (1976) equatorial radius of the Earth
# from section 3.351 of the Explanatory Supplement, the unit of the Moon's distance
MOON_RADIUS_KM = 1738
EARTH_RADIUS_KM = 6378.140

# The principal phases happen when the Moon's ecliptic longitude is 0°, 90°, 180° or 270° ahead
# of the Sun's. Each intermediate phase lasts until the next principal phase.
PRINCIPAL_PHASES = ("New Moon", "First Quarter", "Full Moon", "Last Quarter")
INTERMEDIATE_PHASES = ("Waxing Crescent", "Waxing Gibbous", "Waning Gibbous", "Waning Crescent")
DEGREES_BETWEEN_PRINCIPAL_PHASES = 90

# The lit fraction changes by up to about 12 percentage points a day, so it is given for one time
ILLUMINATED_PERCENT_TIME = time(12)


class MoonPosition(NamedTuple):
    """Where the Moon is seen from the centre of the Earth, on the equator and equinox of date."""
    right_ascension_deg: float
    declination_deg: float
    distance_earth_radii: float


class MoonDay(NamedTuple):
    """The Moon on one Swiss calendar day at one place."""
    moonrise: Optional[datetime]  # None when the Moon does not rise that day
    moonset: Optional[datetime]   # None when the Moon does not set that day
    phase: str                    # a value of PRINCIPAL_PHASES or INTERMEDIATE_PHASES
    phase_time: Optional[datetime]  # when the principal phase happens, None for an intermediate one
    illuminated_percent: float    # at ILLUMINATED_PERCENT_TIME


def calculate_moon_position(moment: datetime) -> MoonPosition:
    """
    Calculate the right ascension, declination and distance of the Moon at a timezone aware moment.

    The series V, U and W are defined by equation (16) of Van Flandern and Pulkkinen, "Low-precision
    formulae for planetary positions" (1979),

        Δ²U = ρ²       ΔV = ρ sin δ       ΔW = ρ cos δ sin(α - L)

    and give the position by equation (17):

        α = L + sin⁻¹[W / (U - V²)^½]     δ = sin⁻¹(V / U^½)     ρ = Δ U^½

    α, δ, ρ   right ascension, declination and geocentric distance, "the equatorial coordinates
              based on the equinox and equator of date" (section V)
    L         L_M, the mean longitude of the Moon
    Δ         "a scaling factor (constant for each body) which has been removed from the series in
              order to make them uniform in precision", 60.40974 for the Moon (section V)
    """
    arguments, centuries_since_1900 = calculate_arguments(moment)
    v = sum_series(MOON_V, MOON_ARGUMENTS, arguments, centuries_since_1900)
    u = sum_series(MOON_U, MOON_ARGUMENTS, arguments, centuries_since_1900)
    w = sum_series(MOON_W, MOON_ARGUMENTS, arguments, centuries_since_1900)
    right_ascension = arguments["moon_mean_longitude"] + math.asin(w / math.sqrt(u - v**2))
    declination = math.asin(v / math.sqrt(u))
    return MoonPosition(
        right_ascension_deg=math.degrees(right_ascension) % 360,
        declination_deg=math.degrees(declination),
        distance_earth_radii=MOON_DISTANCE_SCALE_EARTH_RADII * math.sqrt(u),
    )


def _calculate_height_above_horizon_deg(moment: datetime, latitude_deg: float, longitude_deg: float) -> float:
    """Return how many degrees the Moon's centre is above the angle at which it rises or sets; negative below."""
    position = calculate_moon_position(moment)
    altitude_deg = calculate_altitude_deg(
        moment, position.right_ascension_deg, position.declination_deg, latitude_deg, longitude_deg
    )
    parallax_deg = math.degrees(math.asin(1 / position.distance_earth_radii))
    radius_deg = math.degrees(math.atan(MOON_RADIUS_KM / (position.distance_earth_radii * EARTH_RADIUS_KM)))
    horizon_altitude_deg = parallax_deg - radius_deg - REFRACTION_AT_HORIZON_DEG
    return altitude_deg - horizon_altitude_deg


def _calculate_moon_ahead_of_sun_deg(moment: datetime) -> float:
    """Return how far the Moon's ecliptic longitude is ahead of the Sun's, from 0° to 360°."""
    arguments, centuries_since_1900 = calculate_arguments(moment)
    moon_plon = sum_series(MOON_PLON, MOON_ARGUMENTS, arguments, centuries_since_1900)
    sun_plon = sum_series(SUN_PLON, SUN_ARGUMENTS, arguments, centuries_since_1900)
    moon_longitude_deg = math.degrees(arguments["moon_mean_longitude"]) + moon_plon / ARCSECONDS_PER_DEGREE
    sun_longitude_deg = math.degrees(arguments["sun_mean_longitude"]) + sun_plon / ARCSECONDS_PER_DEGREE
    return (moon_longitude_deg - sun_longitude_deg) % 360


def _calculate_illuminated_percent(moment: datetime) -> float:
    """Return how much of the Moon's disk is lit, 100 (1 + cos i) / 2 with i the phase angle at the Moon."""
    arguments, centuries_since_1900 = calculate_arguments(moment)
    moon_plon = sum_series(MOON_PLON, MOON_ARGUMENTS, arguments, centuries_since_1900)
    moon_beta = sum_series(MOON_BETA, MOON_ARGUMENTS, arguments, centuries_since_1900)
    sun_plon = sum_series(SUN_PLON, SUN_ARGUMENTS, arguments, centuries_since_1900)
    sun_distance_au = sum_series(SUN_RP, SUN_ARGUMENTS, arguments, centuries_since_1900)
    moon_u = sum_series(MOON_U, MOON_ARGUMENTS, arguments, centuries_since_1900)

    moon_longitude = arguments["moon_mean_longitude"] + math.radians(moon_plon / ARCSECONDS_PER_DEGREE)
    moon_latitude = math.radians(moon_beta / ARCSECONDS_PER_DEGREE)
    moon_distance = MOON_DISTANCE_SCALE_EARTH_RADII * math.sqrt(moon_u)
    # The Sun's ecliptic latitude is 0 in Table 4
    sun_longitude = arguments["sun_mean_longitude"] + math.radians(sun_plon / ARCSECONDS_PER_DEGREE)
    sun_distance = sun_distance_au * EARTH_RADII_PER_ASTRONOMICAL_UNIT

    # Positions from the centre of the Earth, in Earth radii, in ecliptic coordinates
    moon = (
        moon_distance * math.cos(moon_latitude) * math.cos(moon_longitude),
        moon_distance * math.cos(moon_latitude) * math.sin(moon_longitude),
        moon_distance * math.sin(moon_latitude),
    )
    sun = (sun_distance * math.cos(sun_longitude), sun_distance * math.sin(sun_longitude), 0.0)
    moon_to_sun = [sun_axis - moon_axis for sun_axis, moon_axis in zip(sun, moon)]
    moon_to_earth = [-moon_axis for moon_axis in moon]
    dot_product = sum(first * second for first, second in zip(moon_to_sun, moon_to_earth))
    cos_phase_angle = dot_product / (math.dist(sun, moon) * moon_distance)
    # Equation 7.12-32
    return 100 * (1 + cos_phase_angle) / 2


def _find_principal_phase(day_start: datetime, day_end: datetime) -> Optional[Tuple[str, datetime]]:
    """Find the principal phase between two moments less than a quarter month apart, and when it happens."""
    start_ahead_deg = _calculate_moon_ahead_of_sun_deg(day_start)
    advance_deg = (_calculate_moon_ahead_of_sun_deg(day_end) - start_ahead_deg) % 360
    phase_index = int(start_ahead_deg // DEGREES_BETWEEN_PRINCIPAL_PHASES) + 1
    phase_deg = phase_index * DEGREES_BETWEEN_PRINCIPAL_PHASES
    if phase_deg - start_ahead_deg > advance_deg:
        return None

    def calculate_past_phase_deg(moment: datetime) -> float:
        # Negative before the phase and positive after it, also where 360° turns back into 0°
        return (_calculate_moon_ahead_of_sun_deg(moment) - phase_deg + 180) % 360 - 180

    phase_time = find_sign_change(calculate_past_phase_deg, day_start, day_end)
    return PRINCIPAL_PHASES[phase_index % len(PRINCIPAL_PHASES)], phase_time


def calculate_moon_day(day: date, latitude_deg: float, longitude_deg: float) -> MoonDay:
    """Calculate the moonrise, moonset, phase and lit percent on a Swiss calendar day at a place."""
    day_start, day_end = find_swiss_day(day)
    percent_time = datetime.combine(day, ILLUMINATED_PERCENT_TIME, SWISS_TZ)

    def calculate_height(moment: datetime) -> float:
        return _calculate_height_above_horizon_deg(moment, latitude_deg, longitude_deg)

    moonrise, moonset = find_rise_and_set(calculate_height, day_start, day_end)
    principal_phase = _find_principal_phase(day_start, day_end)
    if principal_phase is not None:
        phase, phase_time = principal_phase
    else:
        # Without a principal phase the Moon stays in one intermediate phase all day
        ahead_deg = _calculate_moon_ahead_of_sun_deg(percent_time)
        phase = INTERMEDIATE_PHASES[int(ahead_deg // DEGREES_BETWEEN_PRINCIPAL_PHASES)]
        phase_time = None
    return MoonDay(
        moonrise=moonrise,
        moonset=moonset,
        phase=phase,
        phase_time=phase_time,
        illuminated_percent=_calculate_illuminated_percent(percent_time),
    )
