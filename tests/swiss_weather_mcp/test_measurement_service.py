import pytest


# --- read_current_conditions ---

@pytest.mark.asyncio
async def test_read_current_conditions(measurement_service_fixture):
    answer = await measurement_service_fixture.read_current_conditions("Zurich")

    assert answer == {
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
    }


@pytest.mark.asyncio
async def test_read_current_conditions_values_the_station_lacks(measurement_service_fixture):
    # The Davos station stands where the Davos forecast point is, and has no rain or direction here
    answer = await measurement_service_fixture.read_current_conditions("Davos")

    assert (answer["station"], answer["station_distance_km"], answer["station_altitude_difference_m"]) == ("Davos (1594 m)", 0.0, 0)
    assert answer["rain_last_10_minutes_mm"] is None
    assert (answer["wind_direction_degrees"], answer["compass_point"]) == (None, None)
