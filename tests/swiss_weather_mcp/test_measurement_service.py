from datetime import datetime, timezone

import pytest

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.service import (
    StationValue, _build_canton_rows, _build_sunshine_canton_rows, _calculate_change, _find_highest_last_hour,
    _find_sunshine_class, _sum_last_hour,
)
from swiss_weather_mcp.measurements.source import Station, StationMeasurements

FLUNTERN = Station(
    abbr="SMA", name="Zürich / Fluntern", canton="Zurich", altitude_m=604.0, east_m=2685223.0, north_m=1248410.0,
)
APPENZELL = Station(
    abbr="APP", name="Appenzell", canton="Appenzell Innerrhoden", altitude_m=769.0, east_m=0.0, north_m=0.0,
)


def build_measurements(utc_time: str, values: dict) -> StationMeasurements:
    measured_at = datetime.fromisoformat(utc_time).replace(tzinfo=timezone.utc)
    return StationMeasurements(FLUNTERN, measured_at, values)


# --- read_current ---

@pytest.mark.asyncio
async def test_read_current(measurement_service_fixture):
    answer = await measurement_service_fixture.read_current("Zurich")

    assert answer == {
        "data_from": (
            "The measurements for Zürich (418 m) are those of the MeteoSwiss station Zürich / Fluntern (604 m), "
            "2.1 km away and 186 m higher. It measured the following values at 16:00."
        ),
        "place": "Zürich",
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
        "rain_last_hour_mm": 0.0,
        "strongest_gust_last_hour_kmh": 10.1,
    }


@pytest.mark.asyncio
async def test_read_current_values_the_station_lacks(measurement_service_fixture):
    # The Davos station, next to Davos Dorf, has no rain or direction here
    answer = await measurement_service_fixture.read_current("Davos Dorf")

    assert answer["rain_last_10_minutes_mm"] is None
    assert (answer["wind_direction_degrees"], answer["compass_point"]) == (None, None)
    assert "station Davos (1594 m), 0.5 km away and 30 m higher" in answer["data_from"]


@pytest.mark.asyncio
async def test_read_current_without_earlier_measurements(measurement_service_fixture):
    # The Davos station has published only one row, so nothing to compare with and no full hour
    answer = await measurement_service_fixture.read_current("Davos Dorf")

    assert answer["temperature_c"] == 16.5
    assert answer["temperature_change_last_3_hours_c"] is None
    assert answer["pressure_change_last_3_hours_hpa"] is None
    assert answer["sunshine_last_hour_min"] is None
    assert answer["rain_last_hour_mm"] is None
    assert answer["strongest_gust_last_hour_kmh"] is None


# --- _calculate_change ---

def test_calculate_change_without_a_value_3_hours_ago():
    all_measurements = [
        build_measurements("2026-09-25T11:00", {parameters.TEMPERATURE: None}),
        build_measurements("2026-09-25T14:00", {parameters.TEMPERATURE: 21.0}),
    ]
    assert _calculate_change(all_measurements, parameters.TEMPERATURE) is None


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


@pytest.mark.asyncio
async def test_read_current_for_a_district(measurement_service_fixture):
    # The station is found from the district itself, not from the town around it
    answer = await measurement_service_fixture.read_current("Lindenhof")

    assert answer["data_from"].startswith(
        "The measurements for Lindenhof (428 m) are those of the MeteoSwiss station Zürich / Fluntern (604 m), "
        "2.2 km away and 176 m higher."
    )
    assert answer["place"] == "Lindenhof"


# --- read_extremes ---

@pytest.mark.asyncio
async def test_read_extremes_warmest(measurement_service_fixture):
    # The Uetliberg station has no thermometer, so it is not ranked
    answer = await measurement_service_fixture.read_extremes("warmest")

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
async def test_read_extremes_coldest(measurement_service_fixture):
    answer = await measurement_service_fixture.read_extremes("coldest")
    assert [row["station"] for row in answer["stations"]] == [
        "Davos (1594 m)", "Zürich / Fluntern (604 m)", "Zürich / Kloten (426 m)",
    ]


@pytest.mark.asyncio
async def test_read_extremes_lists_5_stations(mocker, measurement_service_fixture):
    all_measurements = []
    for temperature_c in (11.0, 12.0, 13.0, 14.0, 15.0, 16.0):
        all_measurements.append(build_measurements("2026-09-25T14:00", {parameters.TEMPERATURE: temperature_c}))
    mocker.patch.object(
        measurement_service_fixture.measurement_source, "read_all_stations_latest_measurements",
        return_value=all_measurements,
    )
    answer = await measurement_service_fixture.read_extremes("warmest")
    assert [row["temperature_c"] for row in answer["stations"]] == [16.0, 15.0, 14.0, 13.0, 12.0]


@pytest.mark.asyncio
async def test_read_extremes_windiest(measurement_service_fixture):
    answer = await measurement_service_fixture.read_extremes("windiest")
    assert answer["stations"][0] == {"station": "Davos (1594 m)", "canton": "Graubünden", "gusts_kmh": 28.8}


@pytest.mark.asyncio
async def test_read_extremes_sunniest(measurement_service_fixture):
    # Kloten had only 4 minutes of sun and the other three stations 10, so Graubünden, with Davos at
    # 10 minutes, comes before Zürich, with 8 minutes on average
    answer = await measurement_service_fixture.read_extremes("sunniest")

    no_station = {"rather_sunny": 0, "only_a_little_sunshine": 0, "hardly_any_sunshine": 0}
    assert answer == {
        "extreme": "sunniest",
        "measured_at": "2026-09-25T16:00+02:00",
        "measuring_stations": 4,
        "stations_by_sunshine": {"mostly_sunny": 3, "partly_sunny": 1, **no_station},
        "cantons": [
            {"canton": "Graubünden", "measuring_stations": 1, "mostly_sunny": 1, "partly_sunny": 0, **no_station},
            {"canton": "Zurich", "measuring_stations": 3, "mostly_sunny": 2, "partly_sunny": 1, **no_station},
        ],
    }


@pytest.mark.asyncio
async def test_read_extremes_wettest(measurement_service_fixture):
    # The Uetliberg and Davos stations measure no rain, so they count neither as rainy nor as
    # measuring, and Graubünden has no measuring station left
    answer = await measurement_service_fixture.read_extremes("wettest")

    assert answer == {
        "extreme": "wettest",
        "measured_at": "2026-09-25T16:00+02:00",
        "rainy_stations": 1,
        "measuring_stations": 2,
        "cantons": [{"canton": "Zurich", "rainy_stations": 1, "measuring_stations": 2, "rainy_percent": 50}],
        "stations": [{"station": "Zürich / Kloten (426 m)", "canton": "Zurich", "rain_last_10_minutes_mm": 0.3}],
    }


@pytest.mark.asyncio
async def test_read_extremes_wettest_when_dry(mocker, measurement_service_fixture):
    dry_fluntern = build_measurements("2026-09-25T14:00", {parameters.PRECIPITATION: 0.0})
    mocker.patch.object(
        measurement_service_fixture.measurement_source, "read_all_stations_latest_measurements",
        return_value=[dry_fluntern],
    )
    answer = await measurement_service_fixture.read_extremes("wettest")

    assert (answer["rainy_stations"], answer["measuring_stations"]) == (0, 1)
    # The canton is listed with 0, so it is not mistaken for a canton without measurements
    assert answer["cantons"] == [
        {"canton": "Zurich", "rainy_stations": 0, "measuring_stations": 1, "rainy_percent": 0},
    ]
    assert answer["stations"] == []


@pytest.mark.asyncio
async def test_read_extremes_unknown_extreme(measurement_service_fixture):
    with pytest.raises(CannotAnswerError, match="warmest, coldest, windiest, wettest, sunniest"):
        await measurement_service_fixture.read_extremes("foggiest")


@pytest.mark.asyncio
async def test_read_extremes_without_measurements(mocker, measurement_service_fixture):
    mocker.patch.object(
        measurement_service_fixture.measurement_source, "read_all_stations_latest_measurements", return_value=[],
    )
    with pytest.raises(CannotAnswerError, match="publishes no measurements"):
        await measurement_service_fixture.read_extremes("warmest")


# --- _build_canton_rows ---

def test_build_canton_rows_puts_the_canton_with_more_stations_first():
    # Both cantons are fully rainy, but the share of Zürich rests on two stations
    station_values = [StationValue(APPENZELL, 0.4), StationValue(FLUNTERN, 0.2), StationValue(FLUNTERN, 0.3)]

    rows = _build_canton_rows(station_values, station_values, "rainy")
    assert [(row["canton"], row["measuring_stations"]) for row in rows] == [("Zurich", 2), ("Appenzell Innerrhoden", 1)]


# --- _find_sunshine_class ---

@pytest.mark.parametrize("sunshine_min, sunshine_class_name", (
    (10, "mostly_sunny"),
    (8, "mostly_sunny"),
    (7, "rather_sunny"),
    (5, "rather_sunny"),
    (4, "partly_sunny"),
    (2, "partly_sunny"),
    (1, "only_a_little_sunshine"),
    (0, "hardly_any_sunshine"),
))
def test_find_sunshine_class(sunshine_min, sunshine_class_name):
    assert _find_sunshine_class(sunshine_min) == sunshine_class_name


# --- _build_sunshine_canton_rows ---

def test_build_sunshine_canton_rows_puts_the_sunnier_canton_first():
    # Appenzell had 6 minutes of sun, Zürich 10 and 0, so 5 on average
    station_values = [StationValue(FLUNTERN, 10.0), StationValue(FLUNTERN, 0.0), StationValue(APPENZELL, 6.0)]

    rows = _build_sunshine_canton_rows(station_values)
    assert [row["canton"] for row in rows] == ["Appenzell Innerrhoden", "Zurich"]
    assert rows[1]["mostly_sunny"] == 1
    assert rows[1]["hardly_any_sunshine"] == 1


def test_build_sunshine_canton_rows_puts_the_canton_with_more_stations_first():
    # Both cantons had 10 minutes of sun on average, but the average of Zürich rests on two stations
    station_values = [StationValue(APPENZELL, 10.0), StationValue(FLUNTERN, 10.0), StationValue(FLUNTERN, 10.0)]

    rows = _build_sunshine_canton_rows(station_values)
    assert [(row["canton"], row["measuring_stations"]) for row in rows] == [("Zurich", 2), ("Appenzell Innerrhoden", 1)]
