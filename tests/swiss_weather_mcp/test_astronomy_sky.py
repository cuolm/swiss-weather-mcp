from datetime import date, datetime, timedelta, timezone

import pytest

from swiss_weather_mcp.astronomy.sky import find_crossings, list_minutes_of_day

MIDNIGHT = datetime(2026, 10, 28, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
# Six minutes from midnight, and a made-up altitude at each: rising past 0° and falling past it again
MINUTE_MOMENTS = [MIDNIGHT + index * MINUTE for index in range(6)]
ALTITUDES_DEG = [-2.0, -1.0, 1.0, 3.0, 1.0, -1.0]


# --- list_minutes_of_day ---

@pytest.mark.parametrize("day, hours", (
    (date(2026, 10, 28), 24),
    (date(2027, 3, 28), 23),   # summer time starts
    (date(2026, 10, 25), 25),  # summer time ends
))
def test_list_minutes_of_day(day, hours):
    minute_moments = list_minutes_of_day(day)
    assert len(minute_moments) == hours * 60 + 1
    assert minute_moments[-1] - minute_moments[0] == timedelta(hours=hours)


def test_list_minutes_of_day_starts_at_swiss_midnight():
    # 00:00 Swiss winter time is 23:00 UTC the day before
    assert list_minutes_of_day(date(2026, 10, 28))[0] == datetime(2026, 10, 27, 23, tzinfo=timezone.utc)


# --- find_crossings ---

def test_find_crossings():
    # From -1° to 1° in the second minute, 0° is passed halfway; from 1° to -1° in the fifth too
    rising, falling = find_crossings(MINUTE_MOMENTS, ALTITUDES_DEG, 0)
    assert rising == MIDNIGHT + 1.5 * MINUTE
    assert falling == MIDNIGHT + 4.5 * MINUTE


def test_find_crossings_by_share_of_the_minute():
    # From 1° to 3° in the third minute, 1.5° is passed after a quarter of it
    rising, _ = find_crossings(MINUTE_MOMENTS, ALTITUDES_DEG, 1.5)
    assert rising == MIDNIGHT + 2.25 * MINUTE


def test_find_crossings_without_falling():
    rising, falling = find_crossings(MINUTE_MOMENTS[:4], ALTITUDES_DEG[:4], 0)
    assert rising == MIDNIGHT + 1.5 * MINUTE
    assert falling is None


def test_find_crossings_never_reached():
    assert find_crossings(MINUTE_MOMENTS, ALTITUDES_DEG, 5) == (None, None)
