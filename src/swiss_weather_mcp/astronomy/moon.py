"""
Calculate when the Moon rises and sets at a place, its phase, and how much of it is lit.

Sources:
- The positions of the Moon and the Sun: T. C. Van Flandern and K. F. Pulkkinen, "Low-precision
  formulae for planetary positions", ApJS 41, 391 (1979), section III, equation (17) and Table 4
  (see series.py), https://ui.adsabs.harvard.edu/abs/1979ApJS...41..391V/abstract
- Sidereal time: USNO, "Computing Approximate Sidereal Time", https://aa.usno.navy.mil/faq/GAST
- Hour angle and altitude: USNO, "Computing Altitude and Azimuth", https://aa.usno.navy.mil/faq/alt_az
- When the Moon rises and sets: USNO, "Rise, Set, and Twilight Definitions",
  https://aa.usno.navy.mil/faq/RST_defs
- The phases: USNO, "Phases of the Moon and Percent of the Moon Illuminated",
  https://aa.usno.navy.mil/faq/moon_phases, and the glossary of The Astronomical Almanac,
  https://aa.usno.navy.mil/faq/asa_glossary
- The lit fraction and the radius of the Moon: P. K. Seidelmann (ed.), Explanatory Supplement to the
  Astronomical Almanac (1992), equations 7.12-32 and 7.3-10,
  https://archive.org/details/131123ExplanatorySupplementAstronomicalAlmanac

Like USNO, the times are for a level horizon at sea level.
"""
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Dict, NamedTuple, Optional, Sequence, Tuple

from ..formatting import SWISS_TZ
from .series import (
    MOON_ARGUMENTS, MOON_BETA, MOON_PLON, MOON_U, MOON_V, MOON_W, SUN_ARGUMENTS, SUN_PLON, SUN_RP, COS, Term,
)

# The paper's epoch, 2000 January 1.5, Julian date 2451545.0. Its time is ephemeris time, but the
# paper uses universal time instead, as no object moves 1' in the up to 90 s between them.
J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
DAY = timedelta(days=1)
DAYS_PER_JULIAN_CENTURY = 36525

# The fundamental arguments of section III, in revolutions at J2000 and per day
FUNDAMENTAL_ARGUMENTS = {
    "moon_mean_longitude": (0.606434, 0.03660110129),
    "moon_mean_anomaly": (0.374897, 0.03629164709),
    "moon_argument_of_latitude": (0.259091, 0.03674819520),
    "moon_elongation": (0.827362, 0.03386319198),
    "moon_node": (0.347343, -0.00014709391),
    "sun_mean_longitude": (0.779072, 0.00273790931),
    "sun_mean_anomaly": (0.993126, 0.00273777850),
    "venus_mean_longitude": (0.505498, 0.00445046867),
    "venus_mean_anomaly": (0.140023, 0.00445036173),
    "mars_mean_anomaly": (0.053856, 0.00145561327),
    "jupiter_mean_anomaly": (0.056531, 0.00023080893),
}
# The scaling factor Δ of the Moon, section V, which gives its distance in Earth radii from U
MOON_DISTANCE_SCALE_EARTH_RADII = 60.40974
# Section IV
EARTH_RADII_PER_ASTRONOMICAL_UNIT = 23454.8
ARCSECONDS_PER_DEGREE = 3600

# GMST in hours, from the USNO page, with D the days since J2000
GMST_AT_J2000_HOURS = 18.697375
GMST_HOURS_PER_DAY = 24.065709824279
DEGREES_PER_HOUR = 15

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

# The Moon moves about 0.5° an hour against the stars, so its height changes sign at most once an hour
SEARCH_STEP = timedelta(hours=1)
SEARCH_PRECISION = timedelta(seconds=1)
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


def _find_arguments(moment: datetime) -> Tuple[Dict[str, float], float]:
    """Return the fundamental arguments in radians at a moment, and T, the Julian centuries since 1900."""
    days = (moment - J2000) / DAY
    arguments = {}
    for name, (revolutions_at_j2000, revolutions_per_day) in FUNDAMENTAL_ARGUMENTS.items():
        revolutions = revolutions_at_j2000 + revolutions_per_day * days
        # Whole revolutions are dropped, as the paper advises, to keep the sines precise
        arguments[name] = (revolutions % 1) * 2 * math.pi
    centuries_since_1900 = days / DAYS_PER_JULIAN_CENTURY + 1
    return arguments, centuries_since_1900


def _sum_series(
    terms: Sequence[Term], argument_names: Sequence[str], arguments: Dict[str, float], centuries_since_1900: float
) -> float:
    """
    Add up the terms of a series of Table 4.

    "A term consists of the product of a coefficient, a polynomial function, and a trigonometric
    function" (section II), T is "the only polynomial variable" (section III), and "The trigonometric
    function consists of a sine or cosine of the sum of integer multiples of any number of arguments"
    (section II), so

        series = Σ coefficient · T^t_power · sin or cos(n_1 · a_1 + n_2 · a_2 + ...)

    n_j   the integers of the term (Term.multipliers)
    a_j   the fundamental arguments of section III in radians, such as the mean longitude of the Moon
          L_M = 0.606434 + 0.03660110129 t in revolutions
    t     JD - 2451545.0, the days from 2000 January 1.5 (equation 1)
    T     t / 36525 + 1, the Julian centuries from 1900.0 (equation 2)
    """
    total = 0.0
    for term in terms:
        angle = 0.0
        for multiplier, name in zip(term.multipliers, argument_names):
            angle += multiplier * arguments[name]
        wave = math.cos(angle) if term.function == COS else math.sin(angle)
        total += term.coefficient * centuries_since_1900**term.t_power * wave
    return total


def find_moon_position(moment: datetime) -> MoonPosition:
    """
    Find the right ascension, declination and distance of the Moon at a timezone aware moment.

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
    arguments, centuries_since_1900 = _find_arguments(moment)
    v = _sum_series(MOON_V, MOON_ARGUMENTS, arguments, centuries_since_1900)
    u = _sum_series(MOON_U, MOON_ARGUMENTS, arguments, centuries_since_1900)
    w = _sum_series(MOON_W, MOON_ARGUMENTS, arguments, centuries_since_1900)
    right_ascension = arguments["moon_mean_longitude"] + math.asin(w / math.sqrt(u - v**2))
    declination = math.asin(v / math.sqrt(u))
    return MoonPosition(
        right_ascension_deg=math.degrees(right_ascension) % 360,
        declination_deg=math.degrees(declination),
        distance_earth_radii=MOON_DISTANCE_SCALE_EARTH_RADII * math.sqrt(u),
    )


def _find_height_above_horizon_deg(moment: datetime, latitude_deg: float, longitude_deg: float) -> float:
    """Return how far the Moon's centre is above the height at which it rises or sets; negative below."""
    position = find_moon_position(moment)
    days = (moment - J2000) / DAY
    sidereal_time_hours = (GMST_AT_J2000_HOURS + GMST_HOURS_PER_DAY * days) % 24
    hour_angle = math.radians(sidereal_time_hours * DEGREES_PER_HOUR + longitude_deg - position.right_ascension_deg)
    latitude = math.radians(latitude_deg)
    declination = math.radians(position.declination_deg)
    sin_altitude = (
        math.cos(hour_angle) * math.cos(declination) * math.cos(latitude)
        + math.sin(declination) * math.sin(latitude)
    )
    altitude_deg = math.degrees(math.asin(sin_altitude))

    parallax_deg = math.degrees(math.asin(1 / position.distance_earth_radii))
    radius_deg = math.degrees(math.atan(MOON_RADIUS_KM / (position.distance_earth_radii * EARTH_RADIUS_KM)))
    horizon_altitude_deg = parallax_deg - radius_deg - REFRACTION_AT_HORIZON_DEG
    return altitude_deg - horizon_altitude_deg


def _find_moon_ahead_of_sun_deg(moment: datetime) -> float:
    """Return how far the Moon's ecliptic longitude is ahead of the Sun's, from 0° to 360°."""
    arguments, centuries_since_1900 = _find_arguments(moment)
    moon_plon = _sum_series(MOON_PLON, MOON_ARGUMENTS, arguments, centuries_since_1900)
    sun_plon = _sum_series(SUN_PLON, SUN_ARGUMENTS, arguments, centuries_since_1900)
    moon_longitude_deg = math.degrees(arguments["moon_mean_longitude"]) + moon_plon / ARCSECONDS_PER_DEGREE
    sun_longitude_deg = math.degrees(arguments["sun_mean_longitude"]) + sun_plon / ARCSECONDS_PER_DEGREE
    return (moon_longitude_deg - sun_longitude_deg) % 360


def _find_illuminated_percent(moment: datetime) -> float:
    """Return how much of the Moon's disk is lit, 100 (1 + cos i) / 2 with i the phase angle at the Moon."""
    arguments, centuries_since_1900 = _find_arguments(moment)
    moon_plon = _sum_series(MOON_PLON, MOON_ARGUMENTS, arguments, centuries_since_1900)
    moon_beta = _sum_series(MOON_BETA, MOON_ARGUMENTS, arguments, centuries_since_1900)
    sun_plon = _sum_series(SUN_PLON, SUN_ARGUMENTS, arguments, centuries_since_1900)
    sun_distance_au = _sum_series(SUN_RP, SUN_ARGUMENTS, arguments, centuries_since_1900)
    moon_u = _sum_series(MOON_U, MOON_ARGUMENTS, arguments, centuries_since_1900)

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


def _find_sign_change(function: Callable[[datetime], float], start: datetime, end: datetime) -> datetime:
    """Narrow down the moment a function changes sign between start and end, to SEARCH_PRECISION."""
    start_is_positive = function(start) > 0
    while end - start > SEARCH_PRECISION:
        middle = start + (end - start) / 2
        if (function(middle) > 0) == start_is_positive:
            start = middle
        else:
            end = middle
    return start + (end - start) / 2


def _find_moonrise_and_moonset(
    day_start: datetime, day_end: datetime, latitude_deg: float, longitude_deg: float
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Find the first moonrise and moonset between two moments, each None when there is none."""
    def find_height(moment: datetime) -> float:
        return _find_height_above_horizon_deg(moment, latitude_deg, longitude_deg)

    moonrise: Optional[datetime] = None
    moonset: Optional[datetime] = None
    step_start = day_start
    step_start_height = find_height(step_start)
    while step_start < day_end:
        step_end = min(step_start + SEARCH_STEP, day_end)
        step_end_height = find_height(step_end)
        if moonrise is None and step_start_height <= 0 < step_end_height:
            moonrise = _find_sign_change(find_height, step_start, step_end)
        if moonset is None and step_start_height > 0 >= step_end_height:
            moonset = _find_sign_change(find_height, step_start, step_end)
        step_start, step_start_height = step_end, step_end_height
    return moonrise, moonset


def _find_principal_phase(day_start: datetime, day_end: datetime) -> Optional[Tuple[str, datetime]]:
    """Find the principal phase between two moments less than a quarter month apart, and when it happens."""
    start_ahead_deg = _find_moon_ahead_of_sun_deg(day_start)
    advance_deg = (_find_moon_ahead_of_sun_deg(day_end) - start_ahead_deg) % 360
    phase_index = int(start_ahead_deg // DEGREES_BETWEEN_PRINCIPAL_PHASES) + 1
    phase_deg = phase_index * DEGREES_BETWEEN_PRINCIPAL_PHASES
    if phase_deg - start_ahead_deg > advance_deg:
        return None

    def find_past_phase_deg(moment: datetime) -> float:
        # Negative before the phase and positive after it, also where 360° turns back into 0°
        return (_find_moon_ahead_of_sun_deg(moment) - phase_deg + 180) % 360 - 180

    phase_time = _find_sign_change(find_past_phase_deg, day_start, day_end)
    return PRINCIPAL_PHASES[phase_index % len(PRINCIPAL_PHASES)], phase_time


def find_moon_day(day: date, latitude_deg: float, longitude_deg: float) -> MoonDay:
    """Find the moonrise, moonset, phase and lit percent on a Swiss calendar day at a place."""
    day_start = datetime.combine(day, time(0), SWISS_TZ).astimezone(timezone.utc)
    day_end = datetime.combine(day + DAY, time(0), SWISS_TZ).astimezone(timezone.utc)
    percent_time = datetime.combine(day, ILLUMINATED_PERCENT_TIME, SWISS_TZ)

    moonrise, moonset = _find_moonrise_and_moonset(day_start, day_end, latitude_deg, longitude_deg)
    principal_phase = _find_principal_phase(day_start, day_end)
    if principal_phase is not None:
        phase, phase_time = principal_phase
    else:
        # Without a principal phase the Moon stays in one intermediate phase all day
        ahead_deg = _find_moon_ahead_of_sun_deg(percent_time)
        phase = INTERMEDIATE_PHASES[int(ahead_deg // DEGREES_BETWEEN_PRINCIPAL_PHASES)]
        phase_time = None
    return MoonDay(
        moonrise=moonrise,
        moonset=moonset,
        phase=phase,
        phase_time=phase_time,
        illuminated_percent=_find_illuminated_percent(percent_time),
    )
