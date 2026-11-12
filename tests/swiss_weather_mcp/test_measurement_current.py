from datetime import date, datetime, timedelta, timezone

import pytest

from fakes import MEASUREMENTS_NOW, SLF_STATION_CODE_TO_ROWS
from swiss_weather_mcp.distance import MAX_STATIONS_TRIED
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.current import (
    _calculate_change_last_3_hours, _find_closer_slf_station_measurements, _find_highest_last_hour,
    _find_meteoswiss_station_measurements, _find_snow_surface_temperature_c, _sum_last_hour,
)
from swiss_weather_mcp.measurements.meteoswiss_source import MeteoSwissStation, MeteoSwissStationMeasurements
from swiss_weather_mcp.places.place_source import SwisstopoPlace

UETLIBERG = SwisstopoPlace(
    name="Uetliberg", object_types=("Huegel",), population_band=0, is_exonym=False, altitude_m=869.0,
    east_m=2679455.0, north_m=1245034.0,
)
KLOTEN = SwisstopoPlace(
    name="Kloten", object_types=("Ort",), population_band=7, is_exonym=False, altitude_m=447.0,
    east_m=2686100.0, north_m=1256500.0,
)
ZURICH = SwisstopoPlace(
    name="Zürich", object_types=("Ort",), population_band=9, is_exonym=False, altitude_m=418.0,
    east_m=2683500.0, north_m=1247184.0,
)
MATTERHORN = SwisstopoPlace(
    name="Matterhorn", object_types=("Alpiner Gipfel",), population_band=0, is_exonym=False, altitude_m=4478.0,
    east_m=2617049.0, north_m=1091673.0,
)
# About the weighted distance of the MeteoSwiss station Davos to the Matterhorn, so every SLF station
# of the fakes suits it better
METEOSWISS_STATION_WEIGHTED_DISTANCE_M = 250000.0
FLUNTERN = MeteoSwissStation(
    abbr="SMA", name="Zürich / Fluntern", canton="Zurich", altitude_m=604.0, east_m=2685223.0, north_m=1248410.0,
    data_since=date(1864, 1, 1), is_manual_site=False,
)


def find_station(meteoswiss_source_fixture, abbr: str):
    return meteoswiss_source_fixture._load_stations()[abbr]


def count_downloads(source_fixture, url_part: str) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if url_part in call.args[0]:
            downloads += 1
    return downloads


def build_measurements(utc_time: str, parameter_to_value: dict) -> MeteoSwissStationMeasurements:
    measured_at = datetime.fromisoformat(utc_time).replace(tzinfo=timezone.utc)
    return MeteoSwissStationMeasurements(FLUNTERN, measured_at, parameter_to_value)


# --- _calculate_change_last_3_hours ---

def test_calculate_change_without_a_value_3_hours_ago():
    all_measurements = [
        build_measurements("2026-09-25T11:00", {parameters.TEMPERATURE: None}),
        build_measurements("2026-09-25T14:00", {parameters.TEMPERATURE: 21.0}),
    ]
    assert _calculate_change_last_3_hours(all_measurements, parameters.TEMPERATURE) is None


# --- _sum_last_hour and _find_highest_last_hour ---

def build_last_hour(parameter: str, values: list) -> list:
    """Build the six rows of the hour up to 14:00 with the given values of one parameter."""
    all_measurements = []
    for minute, value in zip(("13:10", "13:20", "13:30", "13:40", "13:50", "14:00"), values):
        all_measurements.append(build_measurements(f"2026-09-25T{minute}", {parameter: value}))
    return all_measurements


def test_sum_last_hour_without_one_value():
    all_measurements = build_last_hour(parameters.SUNSHINE, [10.0, 10.0, 10.0, 10.0, 10.0, None])
    assert _sum_last_hour(all_measurements, parameters.SUNSHINE) is None


def test_sum_last_hour_of_rain():
    all_measurements = build_last_hour(parameters.PRECIPITATION, [0.0, 0.1, 0.2, 0.0, 0.0, 0.0])
    assert _sum_last_hour(all_measurements, parameters.PRECIPITATION) == 0.3


def test_sum_last_hour_with_fewer_than_six_rows():
    all_measurements = build_last_hour(parameters.PRECIPITATION, [0.1, 0.1, 0.1])
    assert _sum_last_hour(all_measurements, parameters.PRECIPITATION) is None


def test_find_highest_last_hour_of_gusts():
    all_measurements = build_last_hour(parameters.WIND_GUSTS, [9.4, 31.7, 7.9, 8.3, 8.6, 10.1])
    assert _find_highest_last_hour(all_measurements, parameters.WIND_GUSTS) == 31.7


# --- _find_meteoswiss_station_measurements ---

def test_find_meteoswiss_station_measurements_skips_a_station_without_temperature(
    meteoswiss_source_fixture, measurement_tools_now_fixture,
):
    # Standing on the Uetliberg, whose station has no thermometer, the next one is Fluntern
    assert _find_meteoswiss_station_measurements(meteoswiss_source_fixture, UETLIBERG)[-1].station.abbr == "SMA"


def test_find_meteoswiss_station_measurements_skips_a_station_without_rows(
    meteoswiss_source_fixture, measurement_tools_now_fixture,
):
    # The Kloten station comes first, but its now file has no rows yet
    assert _find_meteoswiss_station_measurements(meteoswiss_source_fixture, KLOTEN)[-1].station.abbr == "SMA"


def test_find_meteoswiss_station_measurements_skips_a_station_that_stopped_publishing(
    mocker, meteoswiss_source_fixture, measurement_tools_now_fixture,
):
    # Fluntern is the first station for Zürich, but its latest row is 61 minutes old
    fluntern = meteoswiss_source_fixture.read_station_measurements(find_station(meteoswiss_source_fixture, "SMA"))
    davos = meteoswiss_source_fixture.read_station_measurements(find_station(meteoswiss_source_fixture, "DAV"))
    old_fluntern = [fluntern[-1]._replace(measured_at=MEASUREMENTS_NOW - timedelta(minutes=61))]
    mocker.patch.object(meteoswiss_source_fixture, "read_station_measurements", side_effect=[old_fluntern, davos])

    assert _find_meteoswiss_station_measurements(meteoswiss_source_fixture, ZURICH)[-1].station.abbr == "DAV"


def test_find_meteoswiss_station_measurements_tries_only_the_first_stations(
    mocker, meteoswiss_source_fixture, measurement_tools_now_fixture,
):
    # Four stations are in the table, and none of them answers
    read_mock = mocker.patch.object(meteoswiss_source_fixture, "read_station_measurements", return_value=[])
    with pytest.raises(CannotAnswerError, match="published a temperature in the last hour"):
        _find_meteoswiss_station_measurements(meteoswiss_source_fixture, ZURICH)
    assert read_mock.call_count == MAX_STATIONS_TRIED


# --- _find_closer_slf_station_measurements ---

def test_find_closer_slf_station_measurements_skips_stations_without_a_recent_temperature(
    slf_source_fixture, measurement_tools_now_fixture,
):
    # Platthorn has no temperature and Gornergratsee no row in the last hour, so La Cassorte is taken
    measurements = _find_closer_slf_station_measurements(
        slf_source_fixture, MATTERHORN, METEOSWISS_STATION_WEIGHTED_DISTANCE_M
    )

    assert measurements.station.code == "ARO1"
    assert measurements.station.display_name == "La Cassorte (3300 m)"
    assert measurements.measured_at == datetime(2026, 9, 25, 14, tzinfo=timezone.utc)
    assert measurements.temperature_c == -0.713
    # SLF publishes 2.318 and 4.557 m/s
    assert measurements.wind_speed_kmh == pytest.approx(8.3448)
    assert measurements.strongest_gust_kmh == pytest.approx(16.4052)


def test_find_closer_slf_station_measurements_takes_only_stations_better_than_meteoswiss(
    source_fixture, slf_source_fixture, measurement_tools_now_fixture,
):
    # A MeteoSwiss station on the summit itself, at 0 m, suits it better than every SLF station
    assert _find_closer_slf_station_measurements(slf_source_fixture, MATTERHORN, 0.0) is None
    assert count_downloads(source_fixture, "/measurements?") == 0


def test_find_closer_slf_station_measurements_tries_at_most_3_stations(
    mocker, source_fixture, slf_source_fixture, measurement_tools_now_fixture,
):
    # With La Cassorte's rows as old as Gornergratsee's, the first 3 stations all fail, and Triftchumme is not tried
    mocker.patch.dict(SLF_STATION_CODE_TO_ROWS, {"ARO1": SLF_STATION_CODE_TO_ROWS["GOR2"]})

    measurements = _find_closer_slf_station_measurements(
        slf_source_fixture, MATTERHORN, METEOSWISS_STATION_WEIGHTED_DISTANCE_M
    )
    assert measurements is None
    assert count_downloads(source_fixture, "/measurements?") == MAX_STATIONS_TRIED
    assert count_downloads(source_fixture, "/ZER2/") == 0


def test_find_closer_slf_station_measurements_downloads_once(
    source_fixture, slf_source_fixture, measurement_tools_now_fixture,
):
    _find_closer_slf_station_measurements(slf_source_fixture, MATTERHORN, METEOSWISS_STATION_WEIGHTED_DISTANCE_M)
    _find_closer_slf_station_measurements(slf_source_fixture, MATTERHORN, METEOSWISS_STATION_WEIGHTED_DISTANCE_M)

    assert count_downloads(source_fixture, "/imis/stations") == 1
    assert count_downloads(source_fixture, "/ARO1/") == 1


# --- _find_snow_surface_temperature_c ---

def read_latest_slf_measurements(slf_source_fixture, code: str):
    for station in slf_source_fixture.list_stations():
        if station.code == code:
            return slf_source_fixture.read_latest_station_measurements(station)
    raise ValueError(f"No SLF station {code} in the fakes")


def test_find_snow_surface_temperature_with_enough_snow(slf_source_fixture):
    # Gornergratsee has 21 cm of snow
    measurements = read_latest_slf_measurements(slf_source_fixture, "GOR2")
    assert _find_snow_surface_temperature_c(measurements) == -4.002


def test_find_snow_surface_temperature_at_10_cm_of_snow(slf_source_fixture):
    measurements = read_latest_slf_measurements(slf_source_fixture, "GOR2")._replace(snow_depth_cm=10.0)
    assert _find_snow_surface_temperature_c(measurements) == -4.002


def test_find_snow_surface_temperature_below_10_cm_of_snow(slf_source_fixture):
    # Triftchumme has 7 cm of snow
    measurements = read_latest_slf_measurements(slf_source_fixture, "ZER2")
    assert _find_snow_surface_temperature_c(measurements) is None


def test_find_snow_surface_temperature_without_a_snow_depth(slf_source_fixture):
    # The WIND station La Cassorte measures no snow depth
    measurements = read_latest_slf_measurements(slf_source_fixture, "ARO1")
    assert _find_snow_surface_temperature_c(measurements) is None


def test_find_snow_surface_temperature_without_a_value(slf_source_fixture):
    measurements = read_latest_slf_measurements(slf_source_fixture, "GOR2")._replace(snow_surface_temperature_c=None)
    assert _find_snow_surface_temperature_c(measurements) is None
