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
from typing import List, NamedTuple, Optional, Tuple

from ..formatting import SWISS_TZ
from .series import MOON_ARGUMENTS, MOON_BETA, MOON_PLON, MOON_U, MOON_V, MOON_W, SUN_ARGUMENTS, SUN_PLON, SUN_RP
from .sky import (
    ARCSECONDS_PER_DEGREE, calculate_altitude_deg, calculate_centuries_since_1900, calculate_ecliptic_longitude_deg,
    calculate_fundamental_arguments_rad, calculate_trigonometric_series, find_crossings, list_minutes_of_day,
)

# The scaling factor Δ of the Moon, section V, which gives its distance in Earth radii from U
MOON_DISTANCE_SCALE_EARTH_RADII = 60.40974
# 1 astronomical unit is 23454.8 Earth radii (section IV)
EARTH_RADII_PER_ASTRONOMICAL_UNIT = 23454.8

# With its position seen from the centre of the Earth, the Moon rises or sets when its centre is
# 90.5666° + its apparent radius - its horizontal parallax from the zenith (USNO). Rearranged, that is
# when its altitude - parallax + radius is -0.5666°, the refraction at the horizon (34'). Near the
# horizon this is the altitude of its upper edge seen from the place, which then seems to touch it.
MOONRISE_ALTITUDE_DEG = -0.5666
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
    fundamental_arguments_rad = calculate_fundamental_arguments_rad(moment)
    centuries_since_1900 = calculate_centuries_since_1900(moment)

    v = calculate_trigonometric_series(MOON_V, MOON_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900)
    u = calculate_trigonometric_series(MOON_U, MOON_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900)
    w = calculate_trigonometric_series(MOON_W, MOON_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900)

    right_ascension_rad = fundamental_arguments_rad["moon_mean_longitude"] + math.asin(w / math.sqrt(u - v**2))
    declination_rad = math.asin(v / math.sqrt(u))
    return MoonPosition(
        right_ascension_deg=math.degrees(right_ascension_rad) % 360,
        declination_deg=math.degrees(declination_rad),
        distance_earth_radii=MOON_DISTANCE_SCALE_EARTH_RADII * math.sqrt(u),
    )


def _calculate_upper_edge_altitude_deg(moment: datetime, latitude_deg: float, longitude_deg: float) -> float:
    """
    Calculate the Moon's altitude minus its parallax plus its radius at a moment, seen from a place, in degrees.

    It is compared with MOONRISE_ALTITUDE_DEG; near the horizon it is the altitude of the Moon's upper edge.
    """
    position = calculate_moon_position(moment)
    altitude_deg = calculate_altitude_deg(
        moment, position.right_ascension_deg, position.declination_deg, latitude_deg, longitude_deg
    )

    parallax_deg = math.degrees(math.asin(1 / position.distance_earth_radii))
    radius_deg = math.degrees(math.atan(MOON_RADIUS_KM / (position.distance_earth_radii * EARTH_RADIUS_KM)))
    return altitude_deg - parallax_deg + radius_deg


def _calculate_moon_ahead_of_sun_deg(moment: datetime) -> float:
    """Calculate how far the Moon's ecliptic longitude is ahead of the Sun's, from 0° to 360°."""
    fundamental_arguments_rad = calculate_fundamental_arguments_rad(moment)
    centuries_since_1900 = calculate_centuries_since_1900(moment)

    moon_longitude_correction_arcsec = calculate_trigonometric_series(
        MOON_PLON, MOON_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900
    )
    sun_longitude_correction_arcsec = calculate_trigonometric_series(
        SUN_PLON, SUN_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900
    )

    moon_longitude_deg = calculate_ecliptic_longitude_deg(
        fundamental_arguments_rad["moon_mean_longitude"], moon_longitude_correction_arcsec
    )
    sun_longitude_deg = calculate_ecliptic_longitude_deg(
        fundamental_arguments_rad["sun_mean_longitude"], sun_longitude_correction_arcsec
    )
    return (moon_longitude_deg - sun_longitude_deg) % 360


def _calculate_illuminated_percent(moment: datetime) -> float:
    """Return how much of the Moon's disk is lit, 100 (1 + cos i) / 2 with i the phase angle at the Moon."""
    fundamental_arguments_rad = calculate_fundamental_arguments_rad(moment)
    centuries_since_1900 = calculate_centuries_since_1900(moment)

    moon_longitude_correction_arcsec = calculate_trigonometric_series(
        MOON_PLON, MOON_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900
    )
    moon_latitude_arcsec = calculate_trigonometric_series(
        MOON_BETA, MOON_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900
    )
    sun_longitude_correction_arcsec = calculate_trigonometric_series(
        SUN_PLON, SUN_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900
    )
    sun_distance_au = calculate_trigonometric_series(
        SUN_RP, SUN_ARGUMENTS, fundamental_arguments_rad, centuries_since_1900
    )

    moon_longitude_deg = calculate_ecliptic_longitude_deg(
        fundamental_arguments_rad["moon_mean_longitude"], moon_longitude_correction_arcsec
    )
    moon_longitude_rad = math.radians(moon_longitude_deg)
    moon_latitude_rad = math.radians(moon_latitude_arcsec / ARCSECONDS_PER_DEGREE)
    moon_distance_earth_radii = calculate_moon_position(moment).distance_earth_radii
    # The Sun's ecliptic latitude is 0 in Table 4
    sun_longitude_deg = calculate_ecliptic_longitude_deg(
        fundamental_arguments_rad["sun_mean_longitude"], sun_longitude_correction_arcsec
    )
    sun_longitude_rad = math.radians(sun_longitude_deg)
    sun_distance_earth_radii = sun_distance_au * EARTH_RADII_PER_ASTRONOMICAL_UNIT

    # Positions from the centre of the Earth, in ecliptic coordinates
    moon_position_earth_radii = (
        moon_distance_earth_radii * math.cos(moon_latitude_rad) * math.cos(moon_longitude_rad),
        moon_distance_earth_radii * math.cos(moon_latitude_rad) * math.sin(moon_longitude_rad),
        moon_distance_earth_radii * math.sin(moon_latitude_rad),
    )
    sun_position_earth_radii = (
        sun_distance_earth_radii * math.cos(sun_longitude_rad),
        sun_distance_earth_radii * math.sin(sun_longitude_rad),
        0.0,
    )
    moon_to_sun_distance_earth_radii = math.dist(moon_position_earth_radii, sun_position_earth_radii)

    # The phase angle i is the angle at the Moon in the triangle of the Earth, the Moon and the Sun.
    # The law of cosines gives it from the lengths of the three sides.
    cos_phase_angle = (
        moon_to_sun_distance_earth_radii**2 + moon_distance_earth_radii**2 - sun_distance_earth_radii**2
    ) / (2 * moon_to_sun_distance_earth_radii * moon_distance_earth_radii)

    # Equation 7.12-32
    return 100 * (1 + cos_phase_angle) / 2


def _find_principal_phase(minute_moments: List[datetime]) -> Optional[Tuple[str, datetime]]:
    """Find the principal phase during the minutes of one day, and when it happens."""
    ahead_at_start_deg = _calculate_moon_ahead_of_sun_deg(minute_moments[0])
    aheads_deg = []
    for moment in minute_moments:
        ahead_deg = _calculate_moon_ahead_of_sun_deg(moment)
        # Once a month, at New Moon, the angle passes 360° and starts again at 0°; adding 360° keeps
        # the day's values growing, so the search finds the crossing of 360°
        if ahead_deg < ahead_at_start_deg:
            ahead_deg += 360
        aheads_deg.append(ahead_deg)

    # The next principal phase: First Quarter at 90°, Full Moon at 180°, Last Quarter at 270° or New Moon at 360°
    phase_number = int(ahead_at_start_deg // DEGREES_BETWEEN_PRINCIPAL_PHASES) + 1
    phase_deg = phase_number * DEGREES_BETWEEN_PRINCIPAL_PHASES

    phase_time, _ = find_crossings(minute_moments, aheads_deg, phase_deg)
    if phase_time is None:
        return None
    return PRINCIPAL_PHASES[phase_number % len(PRINCIPAL_PHASES)], phase_time


def calculate_moon_day(day: date, latitude_deg: float, longitude_deg: float) -> MoonDay:
    """Calculate the moonrise, moonset, phase and lit percent on a Swiss calendar day at a place."""
    minute_moments = list_minutes_of_day(day)
    percent_time = datetime.combine(day, ILLUMINATED_PERCENT_TIME, SWISS_TZ)

    upper_edge_altitudes_deg = [
        _calculate_upper_edge_altitude_deg(moment, latitude_deg, longitude_deg) for moment in minute_moments
    ]
    moonrise, moonset = find_crossings(minute_moments, upper_edge_altitudes_deg, MOONRISE_ALTITUDE_DEG)

    principal_phase = _find_principal_phase(minute_moments)
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
