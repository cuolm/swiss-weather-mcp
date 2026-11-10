from datetime import date, datetime, timedelta

import pytest

from fakes import MEASUREMENTS_NOW
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements.history import _check_days_before_today, _find_meteoswiss_station_daily_measurements
from swiss_weather_mcp.places.place_source import SwisstopoPlace

UETLIBERG = SwisstopoPlace(
    name="Uetliberg", object_types=("Huegel",), population_band=0, is_exonym=False, altitude_m=869.0,
    east_m=2679455.0, north_m=1245034.0,
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


# --- _find_meteoswiss_station_daily_measurements and _check_days_before_today ---

def test_find_meteoswiss_station_daily_measurements_of_this_year(source_fixture, meteoswiss_source_fixture):
    zurich_days = _find_meteoswiss_station_daily_measurements(meteoswiss_source_fixture, ZURICH, date(2026, 9, 23), date(2026, 9, 24))
    assert [day.measured_at.date() for day in zurich_days] == [date(2026, 9, 23), date(2026, 9, 24)]
    assert zurich_days[0].station.abbr == "SMA"
    # Days of this year need only the small recent file
    assert count_downloads(source_fixture, "_d_historical.csv") == 0


def test_find_meteoswiss_station_daily_measurements_across_the_new_year(meteoswiss_source_fixture):
    # The days come from both files, and the days without a row are left out
    zurich_days = _find_meteoswiss_station_daily_measurements(meteoswiss_source_fixture, ZURICH, date(2025, 12, 31), date(2026, 9, 23))
    assert [day.measured_at.date() for day in zurich_days] == [
        date(2025, 12, 31), date(2026, 9, 22), date(2026, 9, 23),
    ]


def test_find_meteoswiss_station_daily_measurements_skips_a_station_without_temperature(meteoswiss_source_fixture):
    # Standing on the Uetliberg, whose station has no thermometer, the next one is Fluntern
    uetliberg_days = _find_meteoswiss_station_daily_measurements(
        meteoswiss_source_fixture, UETLIBERG, date(2026, 9, 24), date(2026, 9, 24)
    )
    assert uetliberg_days[0].station.abbr == "SMA"


def test_find_meteoswiss_station_daily_measurements_skips_a_station_that_started_later(source_fixture, meteoswiss_source_fixture):
    # The Uetliberg station started in 1991, so for 1975 the next one, Fluntern, is read without a download
    uetliberg_days = _find_meteoswiss_station_daily_measurements(meteoswiss_source_fixture, UETLIBERG, date(1975, 7, 1), date(1975, 7, 1))
    assert uetliberg_days[0].station.abbr == "SMA"
    assert count_downloads(source_fixture, "ogd-smn_ueb_d_historical.csv") == 0


def test_check_days_before_today_refuses_today(measurement_tools_now_fixture):
    with pytest.raises(CannotAnswerError) as raised:
        _check_days_before_today(date(2026, 9, 24), date(2026, 9, 25))
    assert str(raised.value) == (
        "There are no daily measurements for 2026-09-25 yet. MeteoSwiss publishes the values of a day the next "
        "morning, so use measurements_current for now and forecast_daily for today and the days ahead."
    )


def test_find_meteoswiss_station_daily_measurements_refuses_days_not_published_yet(mocker, meteoswiss_source_fixture):
    # A day later, at 14:05 UTC, but MeteoSwiss has not added the row of 2026-09-25 to the recent files
    datetime_mock = mocker.patch("swiss_weather_mcp.measurements.meteoswiss_source.datetime", wraps=datetime)
    datetime_mock.now.return_value = MEASUREMENTS_NOW + timedelta(days=1)
    with pytest.raises(CannotAnswerError) as raised:
        _find_meteoswiss_station_daily_measurements(meteoswiss_source_fixture, ZURICH, date(2026, 9, 25), date(2026, 9, 25))
    assert str(raised.value) == (
        "MeteoSwiss has not published the daily measurements for 2026-09-25 yet, try again later."
    )


def test_find_meteoswiss_station_daily_measurements_refuses_days_no_station_measured(meteoswiss_source_fixture):
    with pytest.raises(CannotAnswerError) as raised:
        _find_meteoswiss_station_daily_measurements(meteoswiss_source_fixture, ZURICH, date(1975, 7, 2), date(1975, 7, 3))
    assert str(raised.value) == (
        "None of the 3 stations tried for Zürich (418 m) has a daily temperature for 1975-07-02 to 1975-07-03."
    )
