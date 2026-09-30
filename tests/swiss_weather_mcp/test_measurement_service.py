from datetime import datetime, timezone

import pytest

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.service import (
    StationValue, _build_canton_rows, _calculate_change, _describe_height_difference, _sum_sunshine_last_hour,
)
from swiss_weather_mcp.measurements.source import Station, StationMeasurements

FLUNTERN = Station(
    abbr="SMA", name="Zürich / Fluntern", canton="Zurich", altitude_m=604.0, east_m=2685223.0, north_m=1248410.0,
)


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


# --- read_current_extremes ---

@pytest.mark.asyncio
async def test_read_current_extremes_warmest(measurement_service_fixture):
    # The Uetliberg station has no thermometer, so it is not ranked
    answer = await measurement_service_fixture.read_current_extremes("warmest")

    assert answer == {
        "extreme": "warmest",
        "measured_at": "2026-09-25T16:00+02:00",
        "stations": [
            {"station": "Zürich / Kloten (426 m)", "canton": "Zurich", "temperature_c": 22.4},
            {"station": "Zürich / Fluntern (604 m)", "canton": "Zurich", "temperature_c": 21.0},
            {"station": "Davos (1594 m)", "canton": "Graubünden", "temperature_c": 16.5},
        ],
    }


@pytest.mark.asyncio
async def test_read_current_extremes_coldest(measurement_service_fixture):
    answer = await measurement_service_fixture.read_current_extremes("coldest")
    assert [row["station"] for row in answer["stations"]] == [
        "Davos (1594 m)", "Zürich / Fluntern (604 m)", "Zürich / Kloten (426 m)",
    ]


@pytest.mark.asyncio
async def test_read_current_extremes_lists_5_stations(mocker, measurement_service_fixture):
    all_measurements = []
    for temperature_c in (11.0, 12.0, 13.0, 14.0, 15.0, 16.0):
        all_measurements.append(build_measurements("2026-09-25T14:00", {parameters.TEMPERATURE: temperature_c}))
    mocker.patch.object(
        measurement_service_fixture.measurement_source, "read_all_stations_latest_measurements",
        return_value=all_measurements,
    )
    answer = await measurement_service_fixture.read_current_extremes("warmest")
    assert [row["temperature_c"] for row in answer["stations"]] == [16.0, 15.0, 14.0, 13.0, 12.0]


@pytest.mark.asyncio
async def test_read_current_extremes_windiest(measurement_service_fixture):
    answer = await measurement_service_fixture.read_current_extremes("windiest")
    assert answer["stations"][0] == {"station": "Davos (1594 m)", "canton": "Graubünden", "gusts_kmh": 28.8}


@pytest.mark.asyncio
async def test_read_current_extremes_sunniest(measurement_service_fixture):
    # Kloten had only 4 minutes of sun, so 2 of the 3 stations in Zürich are sunny
    answer = await measurement_service_fixture.read_current_extremes("sunniest")

    assert answer == {
        "extreme": "sunniest",
        "measured_at": "2026-09-25T16:00+02:00",
        "sunny_stations": 3,
        "measuring_stations": 4,
        "cantons": [
            {"canton": "Graubünden", "sunny_stations": 1, "measuring_stations": 1, "sunny_percent": 100},
            {"canton": "Zurich", "sunny_stations": 2, "measuring_stations": 3, "sunny_percent": 67},
        ],
    }


@pytest.mark.asyncio
async def test_read_current_extremes_wettest(measurement_service_fixture):
    # The Uetliberg and Davos stations measure no rain, so they count neither as rainy nor as
    # measuring, and Graubünden has no measuring station left
    answer = await measurement_service_fixture.read_current_extremes("wettest")

    assert answer == {
        "extreme": "wettest",
        "measured_at": "2026-09-25T16:00+02:00",
        "rainy_stations": 1,
        "measuring_stations": 2,
        "cantons": [{"canton": "Zurich", "rainy_stations": 1, "measuring_stations": 2, "rainy_percent": 50}],
        "stations": [{"station": "Zürich / Kloten (426 m)", "canton": "Zurich", "rain_last_10_minutes_mm": 0.3}],
    }


@pytest.mark.asyncio
async def test_read_current_extremes_wettest_when_dry(mocker, measurement_service_fixture):
    dry_fluntern = build_measurements("2026-09-25T14:00", {parameters.PRECIPITATION: 0.0})
    mocker.patch.object(
        measurement_service_fixture.measurement_source, "read_all_stations_latest_measurements",
        return_value=[dry_fluntern],
    )
    answer = await measurement_service_fixture.read_current_extremes("wettest")

    assert (answer["rainy_stations"], answer["measuring_stations"]) == (0, 1)
    # The canton is listed with 0, so it is not mistaken for a canton without measurements
    assert answer["cantons"] == [
        {"canton": "Zurich", "rainy_stations": 0, "measuring_stations": 1, "rainy_percent": 0},
    ]
    assert answer["stations"] == []


@pytest.mark.asyncio
async def test_read_current_extremes_unknown_extreme(measurement_service_fixture):
    with pytest.raises(CannotAnswerError, match="warmest, coldest, windiest, wettest, sunniest"):
        await measurement_service_fixture.read_current_extremes("foggiest")


@pytest.mark.asyncio
async def test_read_current_extremes_without_measurements(mocker, measurement_service_fixture):
    mocker.patch.object(
        measurement_service_fixture.measurement_source, "read_all_stations_latest_measurements", return_value=[],
    )
    with pytest.raises(CannotAnswerError, match="publishes no measurements"):
        await measurement_service_fixture.read_current_extremes("warmest")


# --- _build_canton_rows ---

def test_build_canton_rows_puts_the_canton_with_more_stations_first():
    # Both cantons are fully sunny, but the share of Zürich rests on two stations
    appenzell = Station(abbr="APP", name="Appenzell", canton="Appenzell Innerrhoden", altitude_m=769.0, east_m=0.0, north_m=0.0)
    station_values = [StationValue(appenzell, 10.0), StationValue(FLUNTERN, 10.0), StationValue(FLUNTERN, 10.0)]

    rows = _build_canton_rows(station_values, station_values, "sunny")
    assert [(row["canton"], row["measuring_stations"]) for row in rows] == [("Zurich", 2), ("Appenzell Innerrhoden", 1)]
