from datetime import datetime, timezone

import pytest

from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.service import (
    _calculate_change, _describe_height_difference, _sum_sunshine_last_hour,
)
from swiss_weather_mcp.measurements.source import Station, StationMeasurements

FLUNTERN = Station(abbr="SMA", name="Zürich / Fluntern", altitude_m=604.0, east_m=2685223.0, north_m=1248410.0)


def build_measurements(utc_time: str, values: dict) -> StationMeasurements:
    measured_at = datetime.fromisoformat(utc_time).replace(tzinfo=timezone.utc)
    return StationMeasurements(FLUNTERN, measured_at, values)


# --- read_current_conditions ---

@pytest.mark.asyncio
async def test_read_current_conditions(measurement_service_fixture):
    answer = await measurement_service_fixture.read_current_conditions("Zurich")

    assert answer == {
        "nearest_station": (
            "Zürich / Fluntern (604 m) is the nearest MeteoSwiss station to Zürich 8001 (409 m), "
            "2.1 km away and 195 m higher. It measured the following values at 16:00."
        ),
        "location": "Zürich 8001 (409 m)",
        "altitude_m": 409.0,
        "station": "Zürich / Fluntern (604 m)",
        "station_distance_km": 2.1,
        "station_altitude_difference_m": 195,
        "measured_at": "2026-09-25T16:00+02:00",
        "temperature_c": 21.0,
        "humidity_percent": 34.3,
        "dew_point_c": 4.7,
        "rain_last_10_minutes_mm": 0.0,
        "sunshine_last_10_minutes_min": 10.0,
        "wind_speed_kmh": 4.7,
        "gusts_kmh": 10.1,
        "wind_direction_degrees": 23.0,
        "pressure_sea_level_hpa": 1021.6,
        "compass_point": "NNE",
        "temperature_change_last_3_hours_c": 2.6,
        "pressure_change_last_3_hours_hpa": -1.2,
        "sunshine_last_hour_min": 44.0,
    }


@pytest.mark.asyncio
async def test_read_current_conditions_values_the_station_lacks(measurement_service_fixture):
    # The Davos station stands where the Davos location point is, and has no rain or direction here
    answer = await measurement_service_fixture.read_current_conditions("Davos")

    assert (answer["station"], answer["station_distance_km"], answer["station_altitude_difference_m"]) == ("Davos (1594 m)", 0.0, 0)
    assert answer["rain_last_10_minutes_mm"] is None
    assert (answer["wind_direction_degrees"], answer["compass_point"]) == (None, None)
    assert "0.0 km away and at the same height" in answer["nearest_station"]


@pytest.mark.asyncio
async def test_read_current_conditions_without_earlier_measurements(measurement_service_fixture):
    # The Davos station has published only one row, so nothing to compare with and no full hour
    answer = await measurement_service_fixture.read_current_conditions("Davos")

    assert answer["temperature_c"] == 16.5
    assert answer["temperature_change_last_3_hours_c"] is None
    assert answer["pressure_change_last_3_hours_hpa"] is None
    assert answer["sunshine_last_hour_min"] is None


# --- _calculate_change ---

def test_calculate_change_without_a_value_3_hours_ago():
    all_measurements = [
        build_measurements("2026-09-25T11:00", {parameters.TEMPERATURE: None}),
        build_measurements("2026-09-25T14:00", {parameters.TEMPERATURE: 21.0}),
    ]
    assert _calculate_change(all_measurements, parameters.TEMPERATURE) is None


# --- _sum_sunshine_last_hour ---

def test_sum_sunshine_last_hour_without_one_value():
    all_measurements = []
    for minute in (10, 20, 30, 40, 50):
        all_measurements.append(build_measurements(f"2026-09-25T13:{minute}", {parameters.SUNSHINE: 10.0}))
    all_measurements.append(build_measurements("2026-09-25T14:00", {parameters.SUNSHINE: None}))
    assert _sum_sunshine_last_hour(all_measurements) is None


# --- _describe_height_difference ---

def test_describe_height_difference():
    assert [_describe_height_difference(difference_m) for difference_m in (195, -801, 0)] == [
        "195 m higher", "801 m lower", "at the same height",
    ]
