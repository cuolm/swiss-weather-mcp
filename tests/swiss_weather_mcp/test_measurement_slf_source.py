from datetime import datetime, timedelta, timezone

import pytest

from fakes import MEASUREMENTS_NOW, SLF_STATION_CODE_TO_ROWS
from swiss_weather_mcp.measurements.slf_source import MAX_STATIONS_TRIED, _calculate_east_m, _calculate_north_m
from swiss_weather_mcp.measurements.snow_values import NewSnow, SnowDepth
from swiss_weather_mcp.places.place_source import SwisstopoPlace

MATTERHORN = SwisstopoPlace(
    name="Matterhorn", object_types=("Alpiner Gipfel",), population_band=0, is_exonym=False, altitude_m=4478.0,
    east_m=2617049.0, north_m=1091673.0,
)
# About the weighted distance of the MeteoSwiss station Davos to the Matterhorn, so every SLF station
# of the fakes suits it better
METEOSWISS_STATION_WEIGHTED_DISTANCE_M = 250000.0
ARCSECONDS_PER_DEGREE = 3600


def count_downloads(source_fixture, url_part: str) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if url_part in call.args[0]:
            downloads += 1
    return downloads


def find_snow_station(slf_source_fixture, code: str):
    for station in slf_source_fixture.list_snow_stations():
        if station.code == code:
            return station
    raise AssertionError(f"no snow station {code}")


def move_now(mocker, later: timedelta) -> None:
    datetime_mock = mocker.patch("swiss_weather_mcp.measurements.slf_source.datetime", wraps=datetime)
    datetime_mock.now.return_value = MEASUREMENTS_NOW + later


# --- _calculate_east_m and _calculate_north_m ---

def test_calculate_east_m_and_north_m_of_the_swisstopo_example():
    # The numerical example of section 1 of the swisstopo formulas: 46° 02' 38.87", 8° 43' 49.79"
    latitude_deg = 46 + 2 / 60 + 38.87 / ARCSECONDS_PER_DEGREE
    longitude_deg = 8 + 43 / 60 + 49.79 / ARCSECONDS_PER_DEGREE
    assert _calculate_east_m(latitude_deg, longitude_deg) == pytest.approx(2699999.76, abs=0.01)
    assert _calculate_north_m(latitude_deg, longitude_deg) == pytest.approx(1099999.97, abs=0.01)


# --- find_closer_slf_station_measurements ---

def test_find_closer_slf_station_measurements_skips_stations_without_a_recent_temperature(slf_source_fixture):
    # Platthorn has no temperature and Gornergratsee no row in the last hour, so La Cassorte is taken
    measurements = slf_source_fixture.find_closer_slf_station_measurements(
        MATTERHORN, METEOSWISS_STATION_WEIGHTED_DISTANCE_M
    )

    assert measurements.station.code == "ARO1"
    assert measurements.station.display_name == "La Cassorte (3300 m)"
    assert measurements.measured_at == datetime(2026, 9, 25, 14, tzinfo=timezone.utc)
    assert measurements.temperature_c == -0.713
    assert measurements.wind_speed_m_per_s == 2.318
    assert measurements.strongest_gust_m_per_s == 4.557


def test_find_closer_slf_station_measurements_takes_only_stations_better_than_meteoswiss(source_fixture, slf_source_fixture):
    # A MeteoSwiss station on the summit itself, at 0 m, suits it better than every SLF station
    assert slf_source_fixture.find_closer_slf_station_measurements(MATTERHORN, 0.0) is None
    assert count_downloads(source_fixture, "/measurements?") == 0


def test_find_closer_slf_station_measurements_tries_at_most_3_stations(mocker, source_fixture, slf_source_fixture):
    # With La Cassorte's rows as old as Gornergratsee's, the first 3 stations all fail, and Triftchumme is not tried
    mocker.patch.dict(SLF_STATION_CODE_TO_ROWS, {"ARO1": SLF_STATION_CODE_TO_ROWS["GOR2"]})

    measurements = slf_source_fixture.find_closer_slf_station_measurements(
        MATTERHORN, METEOSWISS_STATION_WEIGHTED_DISTANCE_M
    )
    assert measurements is None
    assert count_downloads(source_fixture, "/measurements?") == MAX_STATIONS_TRIED
    assert count_downloads(source_fixture, "/ZER2/") == 0


def test_find_closer_slf_station_measurements_downloads_once(source_fixture, slf_source_fixture):
    slf_source_fixture.find_closer_slf_station_measurements(MATTERHORN, METEOSWISS_STATION_WEIGHTED_DISTANCE_M)
    slf_source_fixture.find_closer_slf_station_measurements(MATTERHORN, METEOSWISS_STATION_WEIGHTED_DISTANCE_M)

    assert count_downloads(source_fixture, "/imis/stations") == 1
    assert count_downloads(source_fixture, "/ARO1/") == 1


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
