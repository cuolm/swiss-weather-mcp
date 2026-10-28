"""
Find where a body is on the sky, how high it is above the horizon, and when it crosses a height.

Sources, linked in the package docstring:
- Time, fundamental arguments and series: Van Flandern and Pulkkinen (1979), section III and
  Table 4 (see series.py)
- Sidereal time: USNO, "Computing Approximate Sidereal Time"
- Hour angle and altitude: USNO, "Computing Altitude and Azimuth"
"""
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from ..formatting import SWISS_TZ
from .series import COS, Term

# The paper's epoch, 2000 January 1.5, Julian date 2451545.0. Its time is ephemeris time, but the
# paper uses universal time instead, as no object moves 1' in the up to 90 s between them.
J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
DAY = timedelta(days=1)
DAYS_PER_JULIAN_CENTURY = 36525
ARCSECONDS_PER_DEGREE = 3600

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

# GMST in hours, from the USNO page, with D the days since J2000
GMST_AT_J2000_HOURS = 18.697375
GMST_HOURS_PER_DAY = 24.065709824279
DEGREES_PER_HOUR = 15

# The Moon moves about 0.5° an hour against the stars and the Sun less, so the height of either
# crosses a value at most once an hour
SEARCH_STEP = timedelta(hours=1)
SEARCH_PRECISION = timedelta(seconds=1)


def find_arguments(moment: datetime) -> Tuple[Dict[str, float], float]:
    """Return the fundamental arguments in radians at a moment, and T, the Julian centuries since 1900."""
    days = (moment - J2000) / DAY
    arguments = {}
    for name, (revolutions_at_j2000, revolutions_per_day) in FUNDAMENTAL_ARGUMENTS.items():
        revolutions = revolutions_at_j2000 + revolutions_per_day * days
        # Whole revolutions are dropped, as the paper advises, to keep the sines precise
        arguments[name] = (revolutions % 1) * 2 * math.pi
    centuries_since_1900 = days / DAYS_PER_JULIAN_CENTURY + 1
    return arguments, centuries_since_1900


def sum_series(
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


def find_altitude_deg(
    moment: datetime, right_ascension_deg: float, declination_deg: float, latitude_deg: float, longitude_deg: float
) -> float:
    """Return how high a body at a right ascension and declination is above the horizon of a place."""
    days = (moment - J2000) / DAY
    sidereal_time_hours = (GMST_AT_J2000_HOURS + GMST_HOURS_PER_DAY * days) % 24
    hour_angle = math.radians(sidereal_time_hours * DEGREES_PER_HOUR + longitude_deg - right_ascension_deg)
    latitude = math.radians(latitude_deg)
    declination = math.radians(declination_deg)
    sin_altitude = (
        math.cos(hour_angle) * math.cos(declination) * math.cos(latitude)
        + math.sin(declination) * math.sin(latitude)
    )
    return math.degrees(math.asin(sin_altitude))


def find_swiss_day(day: date) -> Tuple[datetime, datetime]:
    """Return the start and end of a Swiss calendar day in UTC, 23, 24 or 25 hours apart."""
    day_start = datetime.combine(day, time(0), SWISS_TZ).astimezone(timezone.utc)
    day_end = datetime.combine(day + DAY, time(0), SWISS_TZ).astimezone(timezone.utc)
    return day_start, day_end


def find_sign_change(function: Callable[[datetime], float], start: datetime, end: datetime) -> datetime:
    """Narrow down the moment a function changes sign between start and end, to SEARCH_PRECISION."""
    start_is_positive = function(start) > 0
    while end - start > SEARCH_PRECISION:
        middle = start + (end - start) / 2
        if (function(middle) > 0) == start_is_positive:
            start = middle
        else:
            end = middle
    return start + (end - start) / 2


def find_rise_and_set(
    find_height: Callable[[datetime], float], start: datetime, end: datetime
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Find the first moment a height turns positive and the first it turns negative between two moments.

    find_height returns how far a body is above the height at which it rises or sets, negative below.
    Each moment is None when there is none.
    """
    rising: Optional[datetime] = None
    setting: Optional[datetime] = None
    step_start = start
    step_start_height = find_height(step_start)
    while step_start < end:
        step_end = min(step_start + SEARCH_STEP, end)
        step_end_height = find_height(step_end)
        if rising is None and step_start_height <= 0 < step_end_height:
            rising = find_sign_change(find_height, step_start, step_end)
        if setting is None and step_start_height > 0 >= step_end_height:
            setting = find_sign_change(find_height, step_start, step_end)
        step_start, step_start_height = step_end, step_end_height
    return rising, setting
