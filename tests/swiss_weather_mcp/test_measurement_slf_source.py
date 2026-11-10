from datetime import datetime, timedelta, timezone


from fakes import MEASUREMENTS_NOW
from swiss_weather_mcp.measurements.snow_values import NewSnow, SnowDepth

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


# --- read_snow_depth ---

def test_read_snow_depth_of_an_imis_station(slf_source_fixture):
    # The last row of Gornergratsee, at 12:30 UTC, is 95 minutes old
    gornergratsee = find_snow_station(slf_source_fixture, "GOR2")
    snow_depth = slf_source_fixture.read_snow_depth(gornergratsee)
    assert snow_depth == SnowDepth(gornergratsee, datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc), 21.412)


def test_read_snow_depth_of_an_imis_station_older_than_210_minutes(mocker, slf_source_fixture):
    gornergratsee = find_snow_station(slf_source_fixture, "GOR2")
    move_now(mocker, timedelta(hours=2))
    assert slf_source_fixture.read_snow_depth(gornergratsee) is None


def test_read_snow_depth_of_a_manual_site(slf_source_fixture):
    davos = find_snow_station(slf_source_fixture, "5DF0")
    snow_depth = slf_source_fixture.read_snow_depth(davos)
    assert snow_depth == SnowDepth(davos, datetime(2026, 9, 25, 6, tzinfo=timezone.utc), 31.0)


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
    assert new_snow == NewSnow(davos, datetime(2026, 9, 25, 6, tzinfo=timezone.utc), 6.0, 16.0)


def test_read_new_snow_of_an_imis_station_at_06_utc(slf_source_fixture):
    # SLF stamps the day at 00:00, but gives its values of 06:00 UTC
    gornergratsee = find_snow_station(slf_source_fixture, "GOR2")
    new_snow = slf_source_fixture.read_new_snow(gornergratsee)
    assert new_snow == NewSnow(gornergratsee, datetime(2026, 9, 25, 6, tzinfo=timezone.utc), 0.0, 0.0)


def test_read_new_snow_without_one_of_the_3_days(slf_source_fixture):
    # Triftchumme has no new snow on 2026-09-24
    triftchumme = find_snow_station(slf_source_fixture, "ZER2")
    new_snow = slf_source_fixture.read_new_snow(triftchumme)
    assert (new_snow.new_snow_24_hours_cm, new_snow.new_snow_3_days_cm) == (0.0, None)


def test_read_new_snow_older_than_yesterday(mocker, slf_source_fixture):
    davos = find_snow_station(slf_source_fixture, "5DF0")
    move_now(mocker, timedelta(days=2))
    assert slf_source_fixture.read_new_snow(davos) is None
