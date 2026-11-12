import os
import time
from datetime import date, datetime, timedelta, timezone


from fakes import MEASUREMENTS_NOW
from swiss_weather_mcp.distance import calculate_weighted_distance_m
from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.meteoswiss_source import (
    MeteoSwissStation, _find_canton_name, _list_periods, _parse_reference_timestamp,
)
from swiss_weather_mcp.measurements.snow_values import SnowDepth
from swiss_weather_mcp.places.place_source import SwisstopoPlace

# How far back a cached file is dated to make it older than the 5 minutes it is kept
OLD_FILE_AGE_SECONDS = 10 * 60
# The same for a recent daily file, kept 1 hour
OLD_RECENT_DAYS_FILE_AGE_SECONDS = 2 * 60 * 60

BRAUNWALD = SwisstopoPlace(
    name="Braunwald", object_types=("Ort", "Haltestelle Bahn"), population_band=4, is_exonym=False,
    altitude_m=1290.0, east_m=2718807.0, north_m=1199989.0,
)
GLARUS = MeteoSwissStation(
    abbr="GLA", name="Glarus", canton="Glarus", altitude_m=517.0, east_m=2723757.0, north_m=1210567.0,
    data_since=date(1864, 1, 1), is_manual_site=False,
)
ELM = MeteoSwissStation(
    abbr="ELM", name="Elm", canton="Glarus", altitude_m=958.0, east_m=2732266.0, north_m=1198424.0,
    data_since=date(1878, 2, 1), is_manual_site=False,
)


def count_downloads(source_fixture, file_name: str) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith(file_name):
            downloads += 1
    return downloads


def find_station(meteoswiss_source_fixture, abbr: str):
    return meteoswiss_source_fixture._load_stations()[abbr]


# --- _find_canton_name ---

def test_find_canton_name():
    # Liechtenstein is listed like a canton, and an abbreviation MeteoSwiss adds later stays as it is
    assert [_find_canton_name(abbreviation) for abbreviation in ("GR", "FL", "XX")] == [
        "Graubünden", "Liechtenstein", "XX",
    ]


# --- _parse_reference_timestamp ---

def test_parse_reference_timestamp():
    assert _parse_reference_timestamp("30.09.2026 07:40") == datetime(2026, 9, 30, 7, 40, tzinfo=timezone.utc)


# --- read_station_measurements ---

def test_read_station_measurements_oldest_first(meteoswiss_source_fixture):
    fluntern = meteoswiss_source_fixture.read_station_measurements(find_station(meteoswiss_source_fixture, "SMA"))
    assert len(fluntern) == 7
    assert fluntern[0].measured_at == datetime(2026, 9, 25, 11, tzinfo=timezone.utc)
    assert fluntern[-1].measured_at == datetime(2026, 9, 25, 14, tzinfo=timezone.utc)
    assert fluntern[-1].parameter_to_value[parameters.TEMPERATURE] == 21.0


def test_read_station_measurements_missing_value(meteoswiss_source_fixture):
    davos = meteoswiss_source_fixture.read_station_measurements(find_station(meteoswiss_source_fixture, "DAV"))[-1]
    assert davos.parameter_to_value[parameters.PRECIPITATION] is None
    assert davos.parameter_to_value[parameters.TEMPERATURE] == 16.5


def test_read_station_measurements_without_rows(meteoswiss_source_fixture):
    kloten = find_station(meteoswiss_source_fixture, "KLO")
    assert meteoswiss_source_fixture.read_station_measurements(kloten) == []


def test_read_station_measurements_downloads_once(source_fixture, meteoswiss_source_fixture):
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    meteoswiss_source_fixture.read_station_measurements(fluntern)
    meteoswiss_source_fixture.read_station_measurements(fluntern)
    assert count_downloads(source_fixture, "ogd-smn_sma_t_now.csv") == 1


def test_read_station_measurements_downloads_again_when_old(source_fixture, meteoswiss_source_fixture, tmp_path):
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    meteoswiss_source_fixture.read_station_measurements(fluntern)
    old_time = time.time() - OLD_FILE_AGE_SECONDS
    os.utime(tmp_path / "measurements" / "ogd-smn_sma_t_now.csv", (old_time, old_time))

    meteoswiss_source_fixture.read_station_measurements(fluntern)
    assert count_downloads(source_fixture, "ogd-smn_sma_t_now.csv") == 2


# --- calculate_weighted_distance_m ---

def test_calculate_weighted_distance_m_prefers_a_station_of_similar_height():
    # Glarus is nearer to Braunwald, but 773 m lower; Elm is 1.8 km farther, but only 332 m lower
    assert calculate_weighted_distance_m(BRAUNWALD, ELM) < calculate_weighted_distance_m(BRAUNWALD, GLARUS)


# --- _list_periods ---

def test_list_periods_reads_only_the_files_that_hold_the_days():
    today = date(2026, 9, 25)
    assert _list_periods(date(2026, 9, 22), date(2026, 9, 24), today) == ["recent"]
    assert _list_periods(date(2025, 12, 30), date(2025, 12, 31), today) == ["historical"]
    assert _list_periods(date(2025, 12, 31), date(2026, 1, 1), today) == ["historical", "recent"]


# --- _read_station_daily_file ---

def test_read_station_daily_file_oldest_first(meteoswiss_source_fixture):
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    fluntern_days = meteoswiss_source_fixture._read_station_daily_file(fluntern, "recent")
    assert [day.measured_at for day in fluntern_days] == [
        datetime(2026, 9, 22, tzinfo=timezone.utc),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
        datetime(2026, 9, 24, tzinfo=timezone.utc),
    ]
    assert fluntern_days[-1].parameter_to_value[parameters.TEMPERATURE_DAY_MAX] == 21.8


def test_read_station_daily_file_missing_values(meteoswiss_source_fixture):
    # On 1975-07-01 Fluntern has the rain only from 6 to 6 UTC, which is not read, and no gust
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    first_day = meteoswiss_source_fixture._read_station_daily_file(fluntern, "historical")[0]
    assert first_day.measured_at == datetime(1975, 7, 1, tzinfo=timezone.utc)
    assert first_day.parameter_to_value[parameters.TEMPERATURE_DAY_MEAN] == 9.8
    assert first_day.parameter_to_value[parameters.PRECIPITATION_DAY] is None
    assert first_day.parameter_to_value[parameters.WIND_GUSTS_DAY] is None


def test_read_station_daily_file_downloads_the_recent_days_again_when_old(
    source_fixture, meteoswiss_source_fixture, tmp_path,
):
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    meteoswiss_source_fixture._read_station_daily_file(fluntern, "recent")
    meteoswiss_source_fixture._read_station_daily_file(fluntern, "recent")
    assert count_downloads(source_fixture, "ogd-smn_sma_d_recent.csv") == 1

    old_time = time.time() - OLD_RECENT_DAYS_FILE_AGE_SECONDS
    os.utime(tmp_path / "measurements" / "ogd-smn_sma_d_recent.csv", (old_time, old_time))
    meteoswiss_source_fixture._read_station_daily_file(fluntern, "recent")
    assert count_downloads(source_fixture, "ogd-smn_sma_d_recent.csv") == 2


# --- list_snow_stations ---

def test_list_snow_stations_leaves_out_stations_without_snow_depth(meteoswiss_source_fixture):
    # UEB measures no snow depth, and KLO's has an end day
    snow_stations = meteoswiss_source_fixture.list_snow_stations()
    assert [station.abbr for station in snow_stations] == ["SMA", "DAV"]


# --- read_snow_depth ---

def test_read_snow_depth(meteoswiss_source_fixture):
    davos = find_station(meteoswiss_source_fixture, "DAV")
    snow_depth = meteoswiss_source_fixture.read_snow_depth(davos)
    assert snow_depth == SnowDepth(davos, datetime(2026, 9, 25, 14, tzinfo=timezone.utc), 12.0)


def test_read_snow_depth_older_than_210_minutes(mocker, meteoswiss_source_fixture):
    # 3 hours 35 minutes after the last row of DAV
    datetime_mock = mocker.patch("swiss_weather_mcp.measurements.meteoswiss_source.datetime", wraps=datetime)
    datetime_mock.now.return_value = MEASUREMENTS_NOW + timedelta(hours=3, minutes=30)
    davos = find_station(meteoswiss_source_fixture, "DAV")
    assert meteoswiss_source_fixture.read_snow_depth(davos) is None


# --- list_manual_sites ---

def test_list_manual_sites_that_measured_the_snow_by_the_last_day(meteoswiss_source_fixture):
    # Stallikon measures the snow since 1998
    manual_sites = meteoswiss_source_fixture.list_manual_sites(date(1975, 7, 1))
    assert [manual_site.abbr for manual_site in manual_sites] == ["SMA", "DAV"]
    assert all(manual_site.is_manual_site for manual_site in manual_sites)


def test_list_manual_sites_takes_the_earliest_day_of_the_data_owners(meteoswiss_source_fixture):
    # Davos passed from MeteoSwiss to SLF in 2006, with one inventory row each
    first_snow_days = meteoswiss_source_fixture._load_manual_site_first_snow_days()
    assert first_snow_days["DAV"] == date(1931, 1, 1)


def test_list_manual_sites_before_any_snow_measurement(meteoswiss_source_fixture):
    assert meteoswiss_source_fixture.list_manual_sites(date(1930, 12, 31)) == []


# --- read_station_daily_measurements of a manual site ---

def test_read_station_daily_measurements_of_a_manual_site(source_fixture, meteoswiss_source_fixture):
    davos = meteoswiss_source_fixture.list_manual_sites(date(2025, 1, 3))[-1]
    all_days = meteoswiss_source_fixture.read_station_daily_measurements(davos, date(2025, 1, 1), date(2025, 1, 3))

    assert all_days[1].measured_at == datetime(2025, 1, 2, tzinfo=timezone.utc)
    assert all_days[1].parameter_to_value == {parameters.SNOW_DEPTH_DAY: 37.0, parameters.NEW_SNOW_DAY: 5.0}
    assert count_downloads(source_fixture, "ogd-nime_dav_d_historical.csv") == 1
    assert count_downloads(source_fixture, "ogd-smn_dav_d_historical.csv") == 0


# --- read_all_stations_latest_measurements ---

def test_read_all_stations_latest_measurements_skips_a_station_not_in_the_table(meteoswiss_source_fixture):
    # MRP is published in the all stations file, but the station table does not say where it is
    all_measurements = meteoswiss_source_fixture.read_all_stations_latest_measurements()
    assert [measurements.station.abbr for measurements in all_measurements] == ["SMA", "UEB", "KLO", "DAV"]


def test_read_all_stations_latest_measurements_time_and_canton(meteoswiss_source_fixture):
    fluntern = meteoswiss_source_fixture.read_all_stations_latest_measurements()[0]
    assert fluntern.measured_at == datetime(2026, 9, 25, 14, tzinfo=timezone.utc)
    assert fluntern.station.canton == "Zurich"


def test_read_all_stations_latest_measurements_missing_value(meteoswiss_source_fixture):
    davos = meteoswiss_source_fixture.read_all_stations_latest_measurements()[3]
    assert davos.parameter_to_value[parameters.PRECIPITATION] is None
    assert davos.parameter_to_value[parameters.TEMPERATURE] == 16.5


def test_read_all_stations_latest_measurements_downloads_once(source_fixture, meteoswiss_source_fixture):
    meteoswiss_source_fixture.read_all_stations_latest_measurements()
    meteoswiss_source_fixture.read_all_stations_latest_measurements()
    assert count_downloads(source_fixture, "VQHA80.csv") == 1


def test_read_all_stations_latest_measurements_downloads_again_when_old(
    source_fixture, meteoswiss_source_fixture, tmp_path,
):
    meteoswiss_source_fixture.read_all_stations_latest_measurements()
    old_time = time.time() - OLD_FILE_AGE_SECONDS
    os.utime(tmp_path / "measurements" / "VQHA80.csv", (old_time, old_time))

    meteoswiss_source_fixture.read_all_stations_latest_measurements()
    assert count_downloads(source_fixture, "VQHA80.csv") == 2
