"""
Calculate where a body is on the sky and its angle above the horizon, and find when an angle passes a value.

Sources, linked in the package docstring:
- Time, fundamental arguments and series: Van Flandern and Pulkkinen (1979), section III and
  Table 4 (see series.py)
- Sidereal time: USNO, "Computing Approximate Sidereal Time"
- Hour angle and altitude: USNO, "Computing Altitude and Azimuth"
"""
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from ..formatting import SWISS_TZ
from .series import COS, Term

# The paper's epoch, 1 January 2000 at 12:00 (2000 January 1.5 in the paper), Julian date 2451545.0.
# The paper's time is ephemeris time, but it says universal time can be used instead, as no object
# moves 1' in the less than 90 s between them.
J2000 = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
DAY = timedelta(days=1)
DAYS_PER_JULIAN_CENTURY = 36525
ARCSECONDS_PER_DEGREE = 3600

# The fundamental arguments of section III, in revolutions at J2000 and per day
FUNDAMENTAL_ARGUMENTS_REVOLUTIONS = {
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

# Greenwich mean sidereal time, how far the Earth has turned, in hours:
# 18.697375 + 24.065709824279 D, with D the days since J2000 (USNO)
GMST_AT_J2000_HOURS = 18.697375
GMST_HOURS_PER_DAY = 24.065709824279
DEGREES_PER_HOUR = 15

# The angles are calculated for every minute of a day. Within a minute they change almost evenly,
# so the moment between two minutes is placed by proportion.
MINUTE = timedelta(minutes=1)


def calculate_fundamental_arguments_rad(moment: datetime) -> Dict[str, float]:
    """Calculate the fundamental arguments of section III at a moment, in radians, by their names."""
    days = (moment - J2000) / DAY
    fundamental_arguments_rad = {}
    for name, (revolutions_at_j2000, revolutions_per_day) in FUNDAMENTAL_ARGUMENTS_REVOLUTIONS.items():
        revolutions = revolutions_at_j2000 + revolutions_per_day * days
        # Whole revolutions are dropped, as the paper advises, to keep the sines precise
        fundamental_arguments_rad[name] = (revolutions % 1) * 2 * math.pi
    return fundamental_arguments_rad


def calculate_centuries_since_1900(moment: datetime) -> float:
    """Calculate T, the Julian centuries since 1900.0 at a moment (equation 2)."""
    days = (moment - J2000) / DAY
    return days / DAYS_PER_JULIAN_CENTURY + 1


def calculate_trigonometric_series(
    terms: Sequence[Term],
    argument_names: Sequence[str],
    fundamental_arguments_rad: Dict[str, float],
    centuries_since_1900: float,
) -> float:
    """
    Calculate the value of a trigonometric series of Table 4, the sum of its terms.

    "A term consists of the product of a coefficient, a polynomial function, and a trigonometric
    function" (section II), T is "the only polynomial variable" (section III), and "The trigonometric
    function consists of a sine or cosine of the sum of integer multiples of any number of arguments"
    (section II), so

        series = Σ coefficient · T^t_power · sin or cos(n_1 · a_1 + n_2 · a_2 + ...)

    n_j   the integers of the term (Term.multipliers)
    a_j   the fundamental arguments of section III in radians, such as the mean longitude of the Moon
          L_M = 0.606434 + 0.03660110129 t in revolutions
    t     JD - 2451545.0, the days from 1 January 2000 at 12:00 (equation 1)
    T     t / 36525 + 1, the Julian centuries from 1900.0 (equation 2)
    """
    total = 0.0
    for term in terms:
        angle_rad = 0.0
        for multiplier, name in zip(term.multipliers, argument_names):
            angle_rad += multiplier * fundamental_arguments_rad[name]
        wave = math.cos(angle_rad) if term.function == COS else math.sin(angle_rad)
        total += term.coefficient * centuries_since_1900**term.t_power * wave
    return total


def calculate_ecliptic_longitude_deg(mean_longitude_rad: float, longitude_correction_arcsec: float) -> float:
    """
    Calculate an ecliptic longitude λ = L + PLON (section IV), in degrees.

    mean_longitude_rad is L in radians, and longitude_correction_arcsec the value of a PLON series in
    arcseconds.
    """
    return math.degrees(mean_longitude_rad) + longitude_correction_arcsec / ARCSECONDS_PER_DEGREE


def calculate_altitude_deg(
    moment: datetime, right_ascension_deg: float, declination_deg: float, latitude_deg: float, longitude_deg: float
) -> float:
    """
    Return the altitude of a body at a right ascension and declination, seen from a place.

    The altitude is the angle above the horizon in degrees: 0 on the horizon, 90 straight overhead,
    negative below the horizon.
    """
    days = (moment - J2000) / DAY
    sidereal_time_hours = (GMST_AT_J2000_HOURS + GMST_HOURS_PER_DAY * days) % 24
    hour_angle_rad = math.radians(sidereal_time_hours * DEGREES_PER_HOUR + longitude_deg - right_ascension_deg)

    latitude_rad = math.radians(latitude_deg)
    declination_rad = math.radians(declination_deg)
    sin_altitude = (
        math.cos(hour_angle_rad) * math.cos(declination_rad) * math.cos(latitude_rad)
        + math.sin(declination_rad) * math.sin(latitude_rad)
    )
    return math.degrees(math.asin(sin_altitude))


def list_minutes_of_day(day: date) -> List[datetime]:
    """List every minute of a Swiss calendar day in UTC, from its 00:00 to the next day's 00:00 included."""
    moment = datetime.combine(day, time(0), SWISS_TZ).astimezone(timezone.utc)
    day_end = datetime.combine(day + DAY, time(0), SWISS_TZ).astimezone(timezone.utc)

    # A day has 23, 24 or 25 hours, as summer time starts or ends
    minute_moments = []
    while moment <= day_end:
        minute_moments.append(moment)
        moment += MINUTE
    return minute_moments


def _find_moment_within_minute(
    moment: datetime, angle_deg: float, next_angle_deg: float, target_deg: float
) -> datetime:
    """
    Find when target_deg is passed within the minute from moment, assuming the angle changes evenly in it.

    The angle goes from angle_deg at moment to next_angle_deg a minute later, so target_deg is passed
    after the share of the minute that it lies along the way.
    """
    share = (target_deg - angle_deg) / (next_angle_deg - angle_deg)
    return moment + share * MINUTE


def find_crossings(
    minute_moments: Sequence[datetime], angles_deg: Sequence[float], target_deg: float
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Find the first moment an angle rises past target_deg and the first moment it falls past it.

    minute_moments are one minute apart, and angles_deg holds the angle at each of them, in degrees. Each moment
    is None when it does not happen.
    """
    rising: Optional[datetime] = None
    falling: Optional[datetime] = None
    for index in range(len(minute_moments) - 1):
        angle_deg = angles_deg[index]
        next_angle_deg = angles_deg[index + 1]
        if rising is None and angle_deg < target_deg <= next_angle_deg:
            rising = _find_moment_within_minute(minute_moments[index], angle_deg, next_angle_deg, target_deg)
        if falling is None and angle_deg >= target_deg > next_angle_deg:
            falling = _find_moment_within_minute(minute_moments[index], angle_deg, next_angle_deg, target_deg)
    return rising, falling
