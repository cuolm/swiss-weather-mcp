import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from fakes import MEASUREMENTS_NOW
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.source import (
    MAX_STATIONS_TRIED, Station, _calculate_weighted_distance_m, _find_canton_name, _parse_reference_timestamp,
)
from swiss_weather_mcp.places.source import SwisstopoPlace

# How far back a cached file is dated to make it older than the 5 minutes it is kept
OLD_FILE_AGE_SECONDS = 10 * 60

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
GLARUS = Station(
    abbr="GLA", name="Glarus", canton="Glarus", altitude_m=517.0, east_m=2723757.0, north_m=1210567.0,
)
ELM = Station(
    abbr="ELM", name="Elm", canton="Glarus", altitude_m=958.0, east_m=2732266.0, north_m=1198424.0,
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


def find_station(measurement_source_fixture, abbr: str):
    return measurement_source_fixture._load_stations()[abbr]


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

def test_read_station_measurements_oldest_first(measurement_source_fixture):
    fluntern = measurement_source_fixture._read_station_measurements(find_station(measurement_source_fixture, "SMA"))
    assert len(fluntern) == 7
    assert fluntern[0].measured_at == datetime(2026, 9, 25, 11, tzinfo=timezone.utc)
    assert fluntern[-1].measured_at == datetime(2026, 9, 25, 14, tzinfo=timezone.utc)
    assert fluntern[-1].values[parameters.TEMPERATURE] == 21.0


def test_read_station_measurements_missing_value(measurement_source_fixture):
    davos = measurement_source_fixture._read_station_measurements(find_station(measurement_source_fixture, "DAV"))[-1]
    assert davos.values[parameters.PRECIPITATION] is None
    assert davos.values[parameters.TEMPERATURE] == 16.5


def test_read_station_measurements_without_rows(measurement_source_fixture):
    kloten = find_station(measurement_source_fixture, "KLO")
    assert measurement_source_fixture._read_station_measurements(kloten) == []


def test_read_station_measurements_downloads_once(source_fixture, measurement_source_fixture):
    fluntern = find_station(measurement_source_fixture, "SMA")
    measurement_source_fixture._read_station_measurements(fluntern)
    measurement_source_fixture._read_station_measurements(fluntern)
    assert count_downloads(source_fixture, "ogd-smn_sma_t_now.csv") == 1


def test_read_station_measurements_downloads_again_when_old(source_fixture, measurement_source_fixture, tmp_path):
    fluntern = find_station(measurement_source_fixture, "SMA")
    measurement_source_fixture._read_station_measurements(fluntern)
    old_time = time.time() - OLD_FILE_AGE_SECONDS
    os.utime(tmp_path / "measurements" / "ogd-smn_sma_t_now.csv", (old_time, old_time))

    measurement_source_fixture._read_station_measurements(fluntern)
    assert count_downloads(source_fixture, "ogd-smn_sma_t_now.csv") == 2


# --- _calculate_weighted_distance_m ---

def test_calculate_weighted_distance_m_prefers_a_station_of_similar_height():
    # Glarus is nearer to Braunwald, but 773 m lower; Elm is 1.8 km farther, but only 332 m lower
    assert _calculate_weighted_distance_m(BRAUNWALD, ELM) < _calculate_weighted_distance_m(BRAUNWALD, GLARUS)


# --- find_station_measurements ---

def test_find_station_measurements_skips_a_station_without_temperature(measurement_source_fixture):
    # Standing on the Uetliberg, whose station has no thermometer, the next one is Fluntern
    assert measurement_source_fixture.find_station_measurements(UETLIBERG)[-1].station.abbr == "SMA"


def test_find_station_measurements_skips_a_station_without_rows(measurement_source_fixture):
    # The Kloten station comes first, but its now file has no rows yet
    assert measurement_source_fixture.find_station_measurements(KLOTEN)[-1].station.abbr == "SMA"


def test_find_station_measurements_skips_a_station_that_stopped_publishing(
    mocker, measurement_source_fixture,
):
    # Fluntern is the first station for Zürich, but its latest row is 61 minutes old
    fluntern = measurement_source_fixture._read_station_measurements(find_station(measurement_source_fixture, "SMA"))
    davos = measurement_source_fixture._read_station_measurements(find_station(measurement_source_fixture, "DAV"))
    old_fluntern = [fluntern[-1]._replace(measured_at=MEASUREMENTS_NOW - timedelta(minutes=61))]
    mocker.patch.object(measurement_source_fixture, "_read_station_measurements", side_effect=[old_fluntern, davos])

    assert measurement_source_fixture.find_station_measurements(ZURICH)[-1].station.abbr == "DAV"


def test_find_station_measurements_tries_only_the_first_stations(
    mocker, measurement_source_fixture,
):
    # Four stations are in the table, and none of them answers
    read_mock = mocker.patch.object(measurement_source_fixture, "_read_station_measurements", return_value=[])
    with pytest.raises(CannotAnswerError, match="published a temperature in the last hour"):
        measurement_source_fixture.find_station_measurements(ZURICH)
    assert read_mock.call_count == MAX_STATIONS_TRIED


# --- read_all_stations_latest_measurements ---

def test_read_all_stations_latest_measurements_skips_a_station_not_in_the_table(measurement_source_fixture):
    # MRP is published in the all stations file, but the station table does not say where it is
    all_measurements = measurement_source_fixture.read_all_stations_latest_measurements()
    assert [measurements.station.abbr for measurements in all_measurements] == ["SMA", "UEB", "KLO", "DAV"]


def test_read_all_stations_latest_measurements_time_and_canton(measurement_source_fixture):
    fluntern = measurement_source_fixture.read_all_stations_latest_measurements()[0]
    assert fluntern.measured_at == datetime(2026, 9, 25, 14, tzinfo=timezone.utc)
    assert fluntern.station.canton == "Zurich"


def test_read_all_stations_latest_measurements_missing_value(measurement_source_fixture):
    davos = measurement_source_fixture.read_all_stations_latest_measurements()[3]
    assert davos.values[parameters.PRECIPITATION] is None
    assert davos.values[parameters.TEMPERATURE] == 16.5


def test_read_all_stations_latest_measurements_downloads_once(source_fixture, measurement_source_fixture):
    measurement_source_fixture.read_all_stations_latest_measurements()
    measurement_source_fixture.read_all_stations_latest_measurements()
    assert count_downloads(source_fixture, "VQHA80.csv") == 1


def test_read_all_stations_latest_measurements_downloads_again_when_old(
    source_fixture, measurement_source_fixture, tmp_path,
):
    measurement_source_fixture.read_all_stations_latest_measurements()
    old_time = time.time() - OLD_FILE_AGE_SECONDS
    os.utime(tmp_path / "measurements" / "VQHA80.csv", (old_time, old_time))

    measurement_source_fixture.read_all_stations_latest_measurements()
    assert count_downloads(source_fixture, "VQHA80.csv") == 2
