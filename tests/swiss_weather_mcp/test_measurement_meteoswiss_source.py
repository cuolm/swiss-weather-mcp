import os
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from fakes import MEASUREMENTS_NOW
from swiss_weather_mcp.distance import calculate_weighted_distance_m
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.meteoswiss_source import (
    MAX_STATIONS_TRIED, MeteoSwissStation, _find_canton_name, _list_periods, _parse_reference_timestamp,
)
from swiss_weather_mcp.measurements.snow_values import SnowDepth
from swiss_weather_mcp.places.place_source import SwisstopoPlace

# How far back a cached file is dated to make it older than the 5 minutes it is kept
OLD_FILE_AGE_SECONDS = 10 * 60
# The same for a recent daily file, kept 1 hour
OLD_RECENT_DAYS_FILE_AGE_SECONDS = 2 * 60 * 60

UETLIBERG = SwisstopoPlace(
    name="Uetliberg", object_types=("Huegel",), population_band=0, is_exonym=False, altitude_m=869.0,
    east_m=2679455.0, north_m=1245034.0,
)
KLOTEN = SwisstopoPlace(
    name="Kloten", object_types=("Ort",), population_band=7, is_exonym=False, altitude_m=447.0,
    east_m=2686100.0, north_m=1256500.0,
)
BRAUNWALD = SwisstopoPlace(
    name="Braunwald", object_types=("Ort", "Haltestelle Bahn"), population_band=4, is_exonym=False,
    altitude_m=1290.0, east_m=2718807.0, north_m=1199989.0,
)
GLARUS = MeteoSwissStation(
    abbr="GLA", name="Glarus", canton="Glarus", altitude_m=517.0, east_m=2723757.0, north_m=1210567.0,
    data_since=date(1864, 1, 1),
)
ELM = MeteoSwissStation(
    abbr="ELM", name="Elm", canton="Glarus", altitude_m=958.0, east_m=2732266.0, north_m=1198424.0,
    data_since=date(1878, 2, 1),
)
ZURICH = SwisstopoPlace(
    name="Zürich", object_types=("Ort",), population_band=9, is_exonym=False, altitude_m=418.0,
    east_m=2683500.0, north_m=1247184.0,
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


# --- _read_station_measurements ---

def test_read_station_measurements_oldest_first(meteoswiss_source_fixture):
    fluntern = meteoswiss_source_fixture._read_station_measurements(find_station(meteoswiss_source_fixture, "SMA"))
    assert len(fluntern) == 7
    assert fluntern[0].measured_at == datetime(2026, 9, 25, 11, tzinfo=timezone.utc)
    assert fluntern[-1].measured_at == datetime(2026, 9, 25, 14, tzinfo=timezone.utc)
    assert fluntern[-1].parameter_to_value[parameters.TEMPERATURE] == 21.0


def test_read_station_measurements_missing_value(meteoswiss_source_fixture):
    davos = meteoswiss_source_fixture._read_station_measurements(find_station(meteoswiss_source_fixture, "DAV"))[-1]
    assert davos.parameter_to_value[parameters.PRECIPITATION] is None
    assert davos.parameter_to_value[parameters.TEMPERATURE] == 16.5


def test_read_station_measurements_without_rows(meteoswiss_source_fixture):
    kloten = find_station(meteoswiss_source_fixture, "KLO")
    assert meteoswiss_source_fixture._read_station_measurements(kloten) == []


def test_read_station_measurements_downloads_once(source_fixture, meteoswiss_source_fixture):
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    meteoswiss_source_fixture._read_station_measurements(fluntern)
    meteoswiss_source_fixture._read_station_measurements(fluntern)
    assert count_downloads(source_fixture, "ogd-smn_sma_t_now.csv") == 1


def test_read_station_measurements_downloads_again_when_old(source_fixture, meteoswiss_source_fixture, tmp_path):
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    meteoswiss_source_fixture._read_station_measurements(fluntern)
    old_time = time.time() - OLD_FILE_AGE_SECONDS
    os.utime(tmp_path / "measurements" / "ogd-smn_sma_t_now.csv", (old_time, old_time))

    meteoswiss_source_fixture._read_station_measurements(fluntern)
    assert count_downloads(source_fixture, "ogd-smn_sma_t_now.csv") == 2


# --- calculate_weighted_distance_m ---

def test_calculate_weighted_distance_m_prefers_a_station_of_similar_height():
    # Glarus is nearer to Braunwald, but 773 m lower; Elm is 1.8 km farther, but only 332 m lower
    assert calculate_weighted_distance_m(BRAUNWALD, ELM) < calculate_weighted_distance_m(BRAUNWALD, GLARUS)


# --- find_station_measurements ---

def test_find_station_measurements_skips_a_station_without_temperature(meteoswiss_source_fixture):
    # Standing on the Uetliberg, whose station has no thermometer, the next one is Fluntern
    assert meteoswiss_source_fixture.find_station_measurements(UETLIBERG)[-1].station.abbr == "SMA"


def test_find_station_measurements_skips_a_station_without_rows(meteoswiss_source_fixture):
    # The Kloten station comes first, but its now file has no rows yet
    assert meteoswiss_source_fixture.find_station_measurements(KLOTEN)[-1].station.abbr == "SMA"


def test_find_station_measurements_skips_a_station_that_stopped_publishing(
    mocker, meteoswiss_source_fixture,
):
    # Fluntern is the first station for Zürich, but its latest row is 61 minutes old
    fluntern = meteoswiss_source_fixture._read_station_measurements(find_station(meteoswiss_source_fixture, "SMA"))
    davos = meteoswiss_source_fixture._read_station_measurements(find_station(meteoswiss_source_fixture, "DAV"))
    old_fluntern = [fluntern[-1]._replace(measured_at=MEASUREMENTS_NOW - timedelta(minutes=61))]
    mocker.patch.object(meteoswiss_source_fixture, "_read_station_measurements", side_effect=[old_fluntern, davos])

    assert meteoswiss_source_fixture.find_station_measurements(ZURICH)[-1].station.abbr == "DAV"


def test_find_station_measurements_tries_only_the_first_stations(
    mocker, meteoswiss_source_fixture,
):
    # Four stations are in the table, and none of them answers
    read_mock = mocker.patch.object(meteoswiss_source_fixture, "_read_station_measurements", return_value=[])
    with pytest.raises(CannotAnswerError, match="published a temperature in the last hour"):
        meteoswiss_source_fixture.find_station_measurements(ZURICH)
    assert read_mock.call_count == MAX_STATIONS_TRIED


# --- _list_periods ---

def test_list_periods_reads_only_the_files_that_hold_the_days():
    today = date(2026, 9, 25)
    assert _list_periods(date(2026, 9, 22), date(2026, 9, 24), today) == ["recent"]
    assert _list_periods(date(2025, 12, 30), date(2025, 12, 31), today) == ["historical"]
    assert _list_periods(date(2025, 12, 31), date(2026, 1, 1), today) == ["historical", "recent"]


# --- _read_station_days ---

def test_read_station_days_oldest_first(meteoswiss_source_fixture):
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    fluntern_days = meteoswiss_source_fixture._read_station_days(fluntern, "recent")
    assert [day.measured_at for day in fluntern_days] == [
        datetime(2026, 9, 22, tzinfo=timezone.utc),
        datetime(2026, 9, 23, tzinfo=timezone.utc),
        datetime(2026, 9, 24, tzinfo=timezone.utc),
    ]
    assert fluntern_days[-1].parameter_to_value[parameters.TEMPERATURE_DAY_MAX] == 21.8


def test_read_station_days_missing_values(meteoswiss_source_fixture):
    # On 1975-07-01 Fluntern has the rain only from 6 to 6 UTC, which is not read, and no gust
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    first_day = meteoswiss_source_fixture._read_station_days(fluntern, "historical")[0]
    assert first_day.measured_at == datetime(1975, 7, 1, tzinfo=timezone.utc)
    assert first_day.parameter_to_value[parameters.TEMPERATURE_DAY_MEAN] == 9.8
    assert first_day.parameter_to_value[parameters.PRECIPITATION_DAY] is None
    assert first_day.parameter_to_value[parameters.WIND_GUSTS_DAY] is None


def test_read_station_days_downloads_the_recent_days_again_when_old(
    source_fixture, meteoswiss_source_fixture, tmp_path,
):
    fluntern = find_station(meteoswiss_source_fixture, "SMA")
    meteoswiss_source_fixture._read_station_days(fluntern, "recent")
    meteoswiss_source_fixture._read_station_days(fluntern, "recent")
    assert count_downloads(source_fixture, "ogd-smn_sma_d_recent.csv") == 1

    old_time = time.time() - OLD_RECENT_DAYS_FILE_AGE_SECONDS
    os.utime(tmp_path / "measurements" / "ogd-smn_sma_d_recent.csv", (old_time, old_time))
    meteoswiss_source_fixture._read_station_days(fluntern, "recent")
    assert count_downloads(source_fixture, "ogd-smn_sma_d_recent.csv") == 2


# --- find_station_days ---

def test_find_station_days_of_this_year(source_fixture, meteoswiss_source_fixture):
    zurich_days = meteoswiss_source_fixture.find_station_days(ZURICH, date(2026, 9, 23), date(2026, 9, 24))
    assert [day.measured_at.date() for day in zurich_days] == [date(2026, 9, 23), date(2026, 9, 24)]
    assert zurich_days[0].station.abbr == "SMA"
    # Days of this year need only the small recent file
    assert count_downloads(source_fixture, "_d_historical.csv") == 0


def test_find_station_days_across_the_new_year(meteoswiss_source_fixture):
    # The days come from both files, and the days without a row are left out
    zurich_days = meteoswiss_source_fixture.find_station_days(ZURICH, date(2025, 12, 31), date(2026, 9, 23))
    assert [day.measured_at.date() for day in zurich_days] == [
        date(2025, 12, 31), date(2026, 9, 22), date(2026, 9, 23),
    ]


def test_find_station_days_skips_a_station_without_temperature(meteoswiss_source_fixture):
    # Standing on the Uetliberg, whose station has no thermometer, the next one is Fluntern
    uetliberg_days = meteoswiss_source_fixture.find_station_days(UETLIBERG, date(2026, 9, 24), date(2026, 9, 24))
    assert uetliberg_days[0].station.abbr == "SMA"


def test_find_station_days_skips_a_station_that_started_later(source_fixture, meteoswiss_source_fixture):
    # The Uetliberg station started in 1991, so for 1975 the next one, Fluntern, is read without a download
    uetliberg_days = meteoswiss_source_fixture.find_station_days(UETLIBERG, date(1975, 7, 1), date(1975, 7, 1))
    assert uetliberg_days[0].station.abbr == "SMA"
    assert count_downloads(source_fixture, "ogd-smn_ueb_d_historical.csv") == 0


def test_find_station_days_refuses_today(meteoswiss_source_fixture):
    with pytest.raises(CannotAnswerError) as raised:
        meteoswiss_source_fixture.find_station_days(ZURICH, date(2026, 9, 24), date(2026, 9, 25))
    assert str(raised.value) == (
        "There are no daily measurements for 2026-09-25 yet. MeteoSwiss publishes the values of a day the next "
        "morning, so use measurements_current for now and forecast_daily for today and the days ahead."
    )


def test_find_station_days_refuses_days_not_published_yet(mocker, meteoswiss_source_fixture):
    # A day later, at 14:05 UTC, but MeteoSwiss has not added the row of 2026-09-25 to the recent files
    datetime_mock = mocker.patch("swiss_weather_mcp.measurements.meteoswiss_source.datetime", wraps=datetime)
    datetime_mock.now.return_value = MEASUREMENTS_NOW + timedelta(days=1)
    with pytest.raises(CannotAnswerError) as raised:
        meteoswiss_source_fixture.find_station_days(ZURICH, date(2026, 9, 25), date(2026, 9, 25))
    assert str(raised.value) == (
        "MeteoSwiss has not published the daily measurements for 2026-09-25 yet, try again later."
    )


def test_find_station_days_refuses_days_no_station_measured(meteoswiss_source_fixture):
    with pytest.raises(CannotAnswerError) as raised:
        meteoswiss_source_fixture.find_station_days(ZURICH, date(1975, 7, 2), date(1975, 7, 3))
    assert str(raised.value) == (
        "None of the 3 stations tried for Zürich (418 m) has a daily temperature for 1975-07-02 to 1975-07-03."
    )


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
