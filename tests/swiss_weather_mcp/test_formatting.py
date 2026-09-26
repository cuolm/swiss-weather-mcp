from datetime import datetime, timezone

from swiss_weather_mcp.formatting import find_compass_point, format_swiss_time


# --- format_swiss_time ---

def test_format_swiss_time_summer():
    assert format_swiss_time(datetime(2026, 9, 23, 12, tzinfo=timezone.utc)) == "2026-09-23T14:00+02:00"


def test_format_swiss_time_winter():
    assert format_swiss_time(datetime(2026, 12, 23, 12, tzinfo=timezone.utc)) == "2026-12-23T13:00+01:00"


# --- find_compass_point ---

def testfind_compass_point():
    # Each of the 16 points covers 22.5 degrees, and a bearing just short of north wraps round to N
    assert [find_compass_point(degrees) for degrees in (0, 11, 12, 90, 217, 355)] == ["N", "N", "NNE", "E", "SW", "N"]
