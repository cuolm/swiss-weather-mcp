from datetime import datetime, timezone

import pytest

from fakes import SLF_STATION_CODE_TO_ROWS
from swiss_weather_mcp.measurements.slf_source import MAX_STATIONS_TRIED, _calculate_east_m, _calculate_north_m
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
