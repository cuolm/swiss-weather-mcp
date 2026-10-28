from datetime import date, datetime, timezone

import pytest

from swiss_weather_mcp.astronomy.moon import calculate_moon_day, calculate_moon_position
from swiss_weather_mcp.formatting import SWISS_TZ

ZURICH_LATITUDE_DEG = 47.37
ZURICH_LONGITUDE_DEG = 8.54

# The USNO answers for Zürich, as rounded to the minute and percent by
# https://aa.usno.navy.mil/api/rstt/oneday?date=2026-10-27&coords=47.37,8.54&tz=1 and the days after it:
# the day, the moonrise, the moonset and the lit percent
USNO_ZURICH_DAYS = (
    ("2026-10-27", "17:32", "08:51", 98),
    ("2026-10-28", "18:16", "10:14", 93),
    ("2026-10-29", "19:13", "11:30", 85),
    ("2026-10-30", "20:25", "12:31", 76),
    ("2026-10-31", "21:45", "13:17", 66),
    ("2026-11-01", "23:07", "13:51", 55),
    ("2026-11-02", None, "14:16", 43),
    ("2026-11-03", "00:27", "14:36", 33),
    ("2026-11-04", "01:43", "14:54", 23),
    ("2026-11-05", "02:57", "15:10", 15),
    ("2026-11-06", "04:10", "15:27", 8),
    ("2026-11-07", "05:22", "15:44", 4),
    ("2026-11-08", "06:34", "16:05", 1),
    ("2026-11-09", "07:45", "16:30", 0),
    ("2026-11-10", "08:55", "17:02", 2),
    ("2026-11-11", "10:00", "17:43", 5),
    ("2026-11-12", "10:56", "18:33", 9),
    ("2026-11-13", "11:42", "19:31", 15),
    ("2026-11-14", "12:19", "20:36", 23),
    ("2026-11-15", "12:47", "21:43", 31),
    ("2026-11-16", "13:10", "22:52", 40),
    ("2026-11-17", "13:29", None, 50),
    ("2026-11-18", "13:46", "00:01", 60),
    ("2026-11-19", "14:02", "01:10", 69),
    ("2026-11-20", "14:19", "02:22", 79),
    ("2026-11-21", "14:37", "03:36", 87),
    ("2026-11-22", "14:59", "04:55", 93),
    ("2026-11-23", "15:27", "06:19", 98),
    ("2026-11-24", "16:05", "07:45", 100),
    ("2026-11-25", "16:58", "09:07", 99),
)
# The principal phases USNO gives in the same answers
USNO_PRINCIPAL_PHASES = (
    ("Full Moon", "2026-10-26T05:12"),
    ("Last Quarter", "2026-11-01T21:28"),
    ("New Moon", "2026-11-09T08:02"),
    ("First Quarter", "2026-11-17T12:48"),
    ("Full Moon", "2026-11-24T15:53"),
)
# USNO rounds to the minute, and the paper's formulae are good to 1'
RISE_AND_SET_TOLERANCE_MIN = 1
# The paper uses universal time for ephemeris time, which puts the phases up to about 2 minutes late
PHASE_TOLERANCE_MIN = 3
PERCENT_TOLERANCE = 1


def minutes_between(first: datetime, second: datetime) -> float:
    return abs((first - second).total_seconds()) / 60


# --- calculate_moon_position ---

def test_calculate_moon_position_on_the_paper_test_date():
    # Table 3 of the paper, 1969 June 28 at 0h: α 16h29m54s, δ -26°44'16"; the distance is Δ √U with U of Table 2
    position = calculate_moon_position(datetime(1969, 6, 28, tzinfo=timezone.utc))
    assert position.right_ascension_deg == pytest.approx((16 + 29 / 60 + 54 / 3600) * 15, abs=1 / 60)
    assert position.declination_deg == pytest.approx(-(26 + 44 / 60 + 16 / 3600), abs=1 / 60)
    assert position.distance_earth_radii == pytest.approx(60.40974 * 0.87647**0.5, abs=0.001)


# --- calculate_moon_day ---

@pytest.mark.parametrize("day, usno_moonrise, usno_moonset, usno_percent", USNO_ZURICH_DAYS)
def test_calculate_moon_day_matches_usno(day, usno_moonrise, usno_moonset, usno_percent):
    moon_day = calculate_moon_day(date.fromisoformat(day), ZURICH_LATITUDE_DEG, ZURICH_LONGITUDE_DEG)

    for found, usno_time in ((moon_day.moonrise, usno_moonrise), (moon_day.moonset, usno_moonset)):
        if usno_time is None:
            assert found is None
        else:
            usno_moment = datetime.fromisoformat(f"{day}T{usno_time}").replace(tzinfo=SWISS_TZ)
            assert minutes_between(found, usno_moment) <= RISE_AND_SET_TOLERANCE_MIN
    assert moon_day.illuminated_percent == pytest.approx(usno_percent, abs=PERCENT_TOLERANCE)


@pytest.mark.parametrize("phase, usno_time", USNO_PRINCIPAL_PHASES)
def test_calculate_moon_day_finds_the_principal_phase(phase, usno_time):
    usno_moment = datetime.fromisoformat(usno_time).replace(tzinfo=SWISS_TZ)
    moon_day = calculate_moon_day(usno_moment.date(), ZURICH_LATITUDE_DEG, ZURICH_LONGITUDE_DEG)
    assert moon_day.phase == phase
    assert minutes_between(moon_day.phase_time, usno_moment) <= PHASE_TOLERANCE_MIN


@pytest.mark.parametrize("day, phase", (
    ("2026-10-30", "Waning Gibbous"),
    ("2026-11-05", "Waning Crescent"),
    ("2026-11-12", "Waxing Crescent"),
    ("2026-11-20", "Waxing Gibbous"),
))
def test_calculate_moon_day_names_the_intermediate_phase(day, phase):
    moon_day = calculate_moon_day(date.fromisoformat(day), ZURICH_LATITUDE_DEG, ZURICH_LONGITUDE_DEG)
    assert moon_day.phase == phase
    assert moon_day.phase_time is None
