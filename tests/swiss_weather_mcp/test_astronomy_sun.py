from datetime import date, datetime, timezone

import pytest

from swiss_weather_mcp.astronomy.sun import find_sun_day, find_sun_position
from swiss_weather_mcp.formatting import SWISS_TZ

ZURICH_LATITUDE_DEG = 47.37
ZURICH_LONGITUDE_DEG = 8.54

# The USNO answers for Zürich, as rounded to the minute by
# https://aa.usno.navy.mil/api/rstt/oneday?date=2026-10-27&coords=47.37,8.54&tz=1 and the days after it:
# the day, the begin of civil twilight, sunrise, sunset and the end of civil twilight
USNO_ZURICH_DAYS = (
    ("2026-10-27", "06:30", "07:02", "17:17", "17:49"),
    ("2026-10-28", "06:31", "07:03", "17:15", "17:47"),
    ("2026-10-29", "06:33", "07:05", "17:14", "17:46"),
    ("2026-10-30", "06:34", "07:06", "17:12", "17:44"),
    ("2026-10-31", "06:36", "07:08", "17:11", "17:43"),
    ("2026-11-01", "06:37", "07:09", "17:09", "17:41"),
    ("2026-11-02", "06:38", "07:11", "17:08", "17:40"),
    ("2026-11-03", "06:40", "07:12", "17:06", "17:38"),
    ("2026-11-04", "06:41", "07:14", "17:05", "17:37"),
    ("2026-11-05", "06:43", "07:15", "17:03", "17:36"),
    ("2026-11-06", "06:44", "07:17", "17:02", "17:34"),
    ("2026-11-07", "06:46", "07:18", "17:00", "17:33"),
    ("2026-11-08", "06:47", "07:20", "16:59", "17:32"),
    ("2026-11-09", "06:48", "07:21", "16:58", "17:30"),
    ("2026-11-10", "06:50", "07:23", "16:56", "17:29"),
    ("2026-11-11", "06:51", "07:24", "16:55", "17:28"),
    ("2026-11-12", "06:53", "07:26", "16:54", "17:27"),
    ("2026-11-13", "06:54", "07:27", "16:53", "17:26"),
    ("2026-11-14", "06:55", "07:29", "16:51", "17:25"),
    ("2026-11-15", "06:57", "07:30", "16:50", "17:24"),
    ("2026-11-16", "06:58", "07:32", "16:49", "17:23"),
    ("2026-11-17", "06:59", "07:33", "16:48", "17:22"),
    ("2026-11-18", "07:01", "07:34", "16:47", "17:21"),
    ("2026-11-19", "07:02", "07:36", "16:46", "17:20"),
    ("2026-11-20", "07:03", "07:37", "16:45", "17:19"),
    ("2026-11-21", "07:05", "07:39", "16:44", "17:18"),
    ("2026-11-22", "07:06", "07:40", "16:43", "17:17"),
    ("2026-11-23", "07:07", "07:42", "16:43", "17:17"),
    ("2026-11-24", "07:09", "07:43", "16:42", "17:16"),
    ("2026-11-25", "07:10", "07:44", "16:41", "17:15"),
)
# USNO answers far from today and across Switzerland, including the days summer time starts and ends:
# the latitude and longitude, then as above
USNO_OTHER_DAYS = (
    (46.2082, 6.1458, "2000-01-01", "07:44", "08:18", "16:59", "17:34"),   # Genève
    (46.0052, 8.9512, "2027-06-21", "04:55", "05:33", "21:19", "21:57"),   # Lugano
    (46.6240, 10.4453, "2035-12-24", "07:26", "08:01", "16:35", "17:10"),  # Müstair
    (47.5573, 7.5876, "2060-04-10", "06:18", "06:49", "20:13", "20:45"),   # Basel
    (47.3702, 8.5442, "2027-03-28", "06:43", "07:13", "19:49", "20:20"),   # Zürich
    (47.3702, 8.5442, "2027-10-31", "06:35", "07:07", "17:11", "17:43"),   # Zürich
)
# USNO rounds to the minute, and the paper's formulae are good to 1'
TOLERANCE_MIN = 1


def assert_matches_usno(latitude_deg, longitude_deg, day, *usno_times):
    sun_day = find_sun_day(date.fromisoformat(day), latitude_deg, longitude_deg)
    found_times = (sun_day.civil_twilight_begin, sun_day.sunrise, sun_day.sunset, sun_day.civil_twilight_end)
    for found, usno_time in zip(found_times, usno_times):
        usno_moment = datetime.fromisoformat(f"{day}T{usno_time}").replace(tzinfo=SWISS_TZ)
        assert abs((found - usno_moment).total_seconds()) / 60 <= TOLERANCE_MIN


# --- find_sun_position ---

def test_find_sun_position_on_the_paper_test_date():
    # Table 3 of the paper, 1969 June 28 at 0h: α 6h26m41s, δ +23°18'11"
    position = find_sun_position(datetime(1969, 6, 28, tzinfo=timezone.utc))
    assert position.right_ascension_deg == pytest.approx((6 + 26 / 60 + 41 / 3600) * 15, abs=1 / 60)
    assert position.declination_deg == pytest.approx(23 + 18 / 60 + 11 / 3600, abs=1 / 60)


# --- find_sun_day ---

@pytest.mark.parametrize("day, twilight_begin, sunrise, sunset, twilight_end", USNO_ZURICH_DAYS)
def test_find_sun_day_matches_usno_in_zurich(day, twilight_begin, sunrise, sunset, twilight_end):
    assert_matches_usno(ZURICH_LATITUDE_DEG, ZURICH_LONGITUDE_DEG, day, twilight_begin, sunrise, sunset, twilight_end)


@pytest.mark.parametrize("latitude_deg, longitude_deg, day, twilight_begin, sunrise, sunset, twilight_end", USNO_OTHER_DAYS)
def test_find_sun_day_matches_usno_on_other_days(
    latitude_deg, longitude_deg, day, twilight_begin, sunrise, sunset, twilight_end
):
    assert_matches_usno(latitude_deg, longitude_deg, day, twilight_begin, sunrise, sunset, twilight_end)
