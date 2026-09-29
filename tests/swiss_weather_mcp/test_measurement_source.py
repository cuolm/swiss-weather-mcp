import os
import time
from datetime import datetime, timezone

import pytest

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.locations import ForecastPoint
from swiss_weather_mcp.measurements import parameters

# How far back a cached file is dated to make it older than the 5 minutes it is kept
OLD_FILE_AGE_SECONDS = 10 * 60


def count_current_value_downloads(source_fixture) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith("VQHA80.csv"):
            downloads += 1
    return downloads


# --- _read_current_measurements ---

def test_read_current_measurements_skips_a_station_not_in_the_table(measurement_source_fixture):
    # MRP is published in the current values, but the station table does not say where it is
    all_measurements = measurement_source_fixture._read_current_measurements()
    assert [measurements.station.abbr for measurements in all_measurements] == ["SMA", "UEB", "DAV"]


def test_read_current_measurements_time(measurement_source_fixture):
    fluntern = measurement_source_fixture._read_current_measurements()[0]
    assert fluntern.measured_at == datetime(2026, 9, 25, 14, tzinfo=timezone.utc)


def test_read_current_measurements_missing_value(measurement_source_fixture):
    davos = measurement_source_fixture._read_current_measurements()[2]
    assert davos.values[parameters.PRECIPITATION] is None
    assert davos.values[parameters.TEMPERATURE] == 16.5


def test_read_current_measurements_downloads_once(source_fixture, measurement_source_fixture):
    measurement_source_fixture._read_current_measurements()
    measurement_source_fixture._read_current_measurements()
    assert count_current_value_downloads(source_fixture) == 1


def test_read_current_measurements_downloads_again_when_old(source_fixture, measurement_source_fixture, tmp_path):
    measurement_source_fixture._read_current_measurements()
    old_time = time.time() - OLD_FILE_AGE_SECONDS
    os.utime(tmp_path / "measurements" / "VQHA80.csv", (old_time, old_time))

    measurement_source_fixture._read_current_measurements()
    assert count_current_value_downloads(source_fixture) == 2


# --- find_nearest_measurements ---

def test_find_nearest_measurements_skips_a_station_without_temperature(measurement_source_fixture):
    # Standing on the Uetliberg, whose station has no thermometer, the next one is Fluntern
    uetliberg = ForecastPoint(
        point_id="1", point_type_id="3", name="Uetliberg", postal_code="", altitude_m=869.0,
        east_m=2679455.0, north_m=1245034.0,
    )
    assert measurement_source_fixture.find_nearest_measurements(uetliberg).station.abbr == "SMA"


def test_find_nearest_measurements_without_any_temperature(mocker, location_finder_fixture, measurement_source_fixture):
    mocker.patch.object(measurement_source_fixture, "_read_current_measurements", return_value=[])
    with pytest.raises(CannotAnswerError, match="no temperature measurements"):
        measurement_source_fixture.find_nearest_measurements(location_finder_fixture.find_point("8001"))
