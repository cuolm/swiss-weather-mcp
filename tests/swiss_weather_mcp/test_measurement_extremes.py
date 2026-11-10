from datetime import date

import pytest

from swiss_weather_mcp.measurements.extremes import (
    StationValue, _build_canton_rows, _build_sunshine_canton_rows, _find_sunshine_class,
)
from swiss_weather_mcp.measurements.meteoswiss_source import MeteoSwissStation

FLUNTERN = MeteoSwissStation(
    abbr="SMA", name="Zürich / Fluntern", canton="Zurich", altitude_m=604.0, east_m=2685223.0, north_m=1248410.0,
    data_since=date(1864, 1, 1),
)
APPENZELL = MeteoSwissStation(
    abbr="APP", name="Appenzell", canton="Appenzell Innerrhoden", altitude_m=769.0, east_m=0.0, north_m=0.0,
    data_since=date(1864, 1, 1),
)


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
