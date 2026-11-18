from datetime import date, datetime, timedelta, timezone

import requests
from fakes import MEASUREMENTS_NOW

from swiss_weather_mcp.measurements.snow_values import LatestNewSnow, LatestSnowDepth, SnowStationDailyMeasurements


def find_snow_station(slf_source_fixture, code: str):
    for station in slf_source_fixture.list_snow_stations():
        if station.code == code:
            return station
    raise AssertionError(f"no snow station {code}")


def move_now(mocker, later: timedelta) -> None:
    datetime_mock = mocker.patch("swiss_weather_mcp.measurements.slf_source.datetime", wraps=datetime)
    datetime_mock.now.return_value = MEASUREMENTS_NOW + later


# --- list_snow_stations ---

def test_list_snow_stations(slf_source_fixture):
    # The IMIS stations with daily snow, not the WIND stations Platthorn and La Cassorte, and the manual sites
    snow_stations = slf_source_fixture.list_snow_stations()
    assert [(station.code, station.is_manual_site) for station in snow_stations] == [
        ("GOR2", False), ("ZER2", False), ("5DF0", True), ("4ZE0", True),
    ]


def test_list_snow_stations_names_the_canton(slf_source_fixture):
    # SLF's lists give the canton abbreviation, the station gets its full name
    davos = find_snow_station(slf_source_fixture, "5DF0")
    assert davos.canton == "Graubünden"


# --- read_morning_snow ---

def test_read_morning_snow_takes_the_newest_imis_morning(slf_source_fixture):
    # Zermatt's manual site last measured the morning before, so it does not count
    all_morning_snow = slf_source_fixture.read_morning_snow()
    assert [(morning_snow.station.code, morning_snow.snow_depth_cm) for morning_snow in all_morning_snow] == [
        ("GOR2", 21.0), ("ZER2", 8.0), ("5DF0", 31.0),
    ]
    assert {morning_snow.measured_at for morning_snow in all_morning_snow} == {
        datetime(2026, 9, 25, 6, tzinfo=timezone.utc),
    }


def test_read_morning_snow_without_imis_daily_snow(mocker, slf_source_fixture):
    mocker.patch.object(slf_source_fixture, "_read_all_imis_stations_daily_snow", return_value={})
    assert slf_source_fixture.read_morning_snow() == []


# --- read_snow_depth ---

def test_read_snow_depth_of_an_imis_station(slf_source_fixture):
    # The last row of Gornergratsee, at 12:30 UTC, is 95 minutes old
    gornergratsee = find_snow_station(slf_source_fixture, "GOR2")
    snow_depth = slf_source_fixture.read_snow_depth(gornergratsee)
    assert snow_depth == LatestSnowDepth(gornergratsee, datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc), 21.412)


def test_read_snow_depth_of_an_imis_station_older_than_210_minutes(mocker, slf_source_fixture):
    gornergratsee = find_snow_station(slf_source_fixture, "GOR2")
    move_now(mocker, timedelta(hours=2))
    assert slf_source_fixture.read_snow_depth(gornergratsee) is None


def test_read_snow_depth_of_a_manual_site(slf_source_fixture):
    davos = find_snow_station(slf_source_fixture, "5DF0")
    snow_depth = slf_source_fixture.read_snow_depth(davos)
    assert snow_depth == LatestSnowDepth(davos, datetime(2026, 9, 25, 6, tzinfo=timezone.utc), 31.0)


def test_read_snow_depth_of_a_manual_site_from_yesterday(mocker, slf_source_fixture):
    # Before the observer's next morning reading, yesterday's still counts
    davos = find_snow_station(slf_source_fixture, "5DF0")
    move_now(mocker, timedelta(days=1))
    assert slf_source_fixture.read_snow_depth(davos).snow_depth_cm == 31.0


def test_read_snow_depth_of_a_manual_site_older_than_yesterday(slf_source_fixture):
    zermatt = find_snow_station(slf_source_fixture, "4ZE0")
    assert slf_source_fixture.read_snow_depth(zermatt) is None


# --- read_new_snow ---

def test_read_new_snow_of_a_manual_site(slf_source_fixture):
    davos = find_snow_station(slf_source_fixture, "5DF0")
    new_snow = slf_source_fixture.read_new_snow(davos)
    assert new_snow == LatestNewSnow(davos, datetime(2026, 9, 25, 6, tzinfo=timezone.utc), 6.0, 16.0)


def test_read_new_snow_of_an_imis_station_at_06_utc(slf_source_fixture):
    # SLF stamps the day at 00:00, but gives its values of 06:00 UTC
    gornergratsee = find_snow_station(slf_source_fixture, "GOR2")
    new_snow = slf_source_fixture.read_new_snow(gornergratsee)
    assert new_snow == LatestNewSnow(gornergratsee, datetime(2026, 9, 25, 6, tzinfo=timezone.utc), 0.0, 0.0)


def test_read_new_snow_without_one_of_the_3_days(slf_source_fixture):
    # Triftchumme has no new snow on 2026-09-24
    triftchumme = find_snow_station(slf_source_fixture, "ZER2")
    new_snow = slf_source_fixture.read_new_snow(triftchumme)
    assert (new_snow.new_snow_24_hours_cm, new_snow.new_snow_3_days_cm) == (0.0, None)


def test_read_new_snow_older_than_yesterday(mocker, slf_source_fixture):
    davos = find_snow_station(slf_source_fixture, "5DF0")
    move_now(mocker, timedelta(days=2))
    assert slf_source_fixture.read_new_snow(davos) is None


# --- read_snow_history ---

def find_day(daily_measurements, day: date) -> SnowStationDailyMeasurements:
    for day_measurements in daily_measurements:
        if day_measurements.day == day:
            return day_measurements
    raise AssertionError(f"no snow on {day}")


def test_read_snow_history_takes_the_new_snow_from_the_next_row(slf_source_fixture):
    # SLF stamps the new snow at the end of its 24 hours, so the new snow of 2025-01-02 is in the row of 2025-01-03
    davos = find_snow_station(slf_source_fixture, "5DF0")
    daily_measurements = slf_source_fixture.read_snow_history(davos, date(2025, 1, 2))
    assert find_day(daily_measurements, date(2025, 1, 2)) == SnowStationDailyMeasurements(
        davos, date(2025, 1, 2), 39.0, 6.0
    )


def test_read_snow_history_without_the_next_row_value(slf_source_fixture):
    # The row of 2025-01-02 has no new snow, so 2025-01-01 has none either
    davos = find_snow_station(slf_source_fixture, "5DF0")
    daily_measurements = slf_source_fixture.read_snow_history(davos, date(2025, 1, 1))
    assert find_day(daily_measurements, date(2025, 1, 1)) == SnowStationDailyMeasurements(
        davos, date(2025, 1, 1), 40.0, None
    )


def test_read_snow_history_fills_the_newest_days_from_the_api(slf_source_fixture):
    # The history file ends on 2026-09-23; the new snow of that day is in the API's row of 2026-09-24
    davos = find_snow_station(slf_source_fixture, "5DF0")
    daily_measurements = slf_source_fixture.read_snow_history(davos, date(2026, 9, 23))
    assert find_day(daily_measurements, date(2026, 9, 23)) == SnowStationDailyMeasurements(
        davos, date(2026, 9, 23), 18.0, 8.0
    )
    assert daily_measurements[-1] == SnowStationDailyMeasurements(davos, date(2026, 9, 25), 31.0, None)


def test_read_snow_history_of_an_imis_station(slf_source_fixture):
    # An IMIS row stamped 00:00 holds the values of 06:00 UTC that day
    triftchumme = find_snow_station(slf_source_fixture, "ZER2")
    daily_measurements = slf_source_fixture.read_snow_history(triftchumme, date(2026, 9, 22))
    assert find_day(daily_measurements, date(2026, 9, 21)) == SnowStationDailyMeasurements(
        triftchumme, date(2026, 9, 21), 14.0, 0.0
    )
    assert find_day(daily_measurements, date(2026, 9, 22)) == SnowStationDailyMeasurements(
        triftchumme, date(2026, 9, 22), 13.0, 3.8
    )


def test_read_snow_history_takes_the_file_on_a_day_both_have(slf_source_fixture):
    # On 2026-09-22 the API has 12 cm and 0.5 cm of new snow, the history file 13 cm and none
    triftchumme = find_snow_station(slf_source_fixture, "ZER2")
    daily_measurements = slf_source_fixture.read_snow_history(triftchumme, date(2026, 9, 25))
    assert find_day(daily_measurements, date(2026, 9, 22)).snow_depth_cm == 13.0
    assert find_day(daily_measurements, date(2026, 9, 21)).new_snow_cm == 0.0


def test_read_snow_history_of_an_old_day_without_the_api(source_fixture, slf_source_fixture):
    # The history file holds 2025-01-02 and the day after it, so the API is not asked
    davos = find_snow_station(slf_source_fixture, "5DF0")
    slf_source_fixture.read_snow_history(davos, date(2025, 1, 2))
    api_urls = [call.args[0] for call in source_fixture.get_mock.call_args_list if "/station/5DF0/" in call.args[0]]
    assert api_urls == []


def test_read_snow_history_without_the_api(mocker, slf_source_fixture):
    # The history file still answers, only the newest days are missing
    davos = find_snow_station(slf_source_fixture, "5DF0")
    mocker.patch.object(
        slf_source_fixture, "_read_manual_site_daily_snow", side_effect=requests.ConnectionError("connection refused")
    )
    daily_measurements = slf_source_fixture.read_snow_history(davos, date(2026, 9, 25))
    assert find_day(daily_measurements, date(2026, 9, 23)) == SnowStationDailyMeasurements(
        davos, date(2026, 9, 23), 18.0, None
    )
    assert daily_measurements[-1].day == date(2026, 9, 23)
