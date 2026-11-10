from datetime import date, datetime, timezone

from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.current import _calculate_change, _find_highest_last_hour, _sum_last_hour
from swiss_weather_mcp.measurements.meteoswiss_source import MeteoSwissStation, MeteoSwissStationMeasurements

FLUNTERN = MeteoSwissStation(
    abbr="SMA", name="Zürich / Fluntern", canton="Zurich", altitude_m=604.0, east_m=2685223.0, north_m=1248410.0,
    data_since=date(1864, 1, 1),
)


def build_measurements(utc_time: str, parameter_to_value: dict) -> MeteoSwissStationMeasurements:
    measured_at = datetime.fromisoformat(utc_time).replace(tzinfo=timezone.utc)
    return MeteoSwissStationMeasurements(FLUNTERN, measured_at, parameter_to_value)


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
