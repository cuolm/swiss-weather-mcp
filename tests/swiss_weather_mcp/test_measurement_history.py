from datetime import date, datetime, timedelta

import pytest
import requests

from fakes import MEASUREMENTS_NOW
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.history import (
    _build_rain_field, _check_days_before_today, _find_meteoswiss_station_daily_measurements,
    _find_meteoswiss_manual_site_daily_measurements, build_history_answer,
)
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


# --- _build_rain_field ---

def test_build_rain_field_of_the_utc_day():
    # Arosa on 2015-12-16 measured both, so the rain of the UTC day is taken, like the other values
    parameter_to_value = {parameters.PRECIPITATION_DAY: 3.0, parameters.PRECIPITATION_DAY_6_TO_6_UTC: 3.8}
    assert _build_rain_field(parameter_to_value) == {"rain_mm": 3.0}


def test_build_rain_field_from_6_to_6_utc():
    # Arosa on 1999-02-18 did not yet measure the rain per UTC day
    parameter_to_value = {parameters.PRECIPITATION_DAY: None, parameters.PRECIPITATION_DAY_6_TO_6_UTC: 24.5}
    assert _build_rain_field(parameter_to_value) == {"rain_06_to_06_utc_mm": 24.5}


def test_build_rain_field_without_rain():
    # A station without a rain gauge
    parameter_to_value = {parameters.PRECIPITATION_DAY: None, parameters.PRECIPITATION_DAY_6_TO_6_UTC: None}
    assert _build_rain_field(parameter_to_value) == {"rain_mm": None}


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


# --- _find_meteoswiss_manual_site_daily_measurements ---

def test_find_meteoswiss_manual_site_daily_measurements_skips_a_manual_site_without_snow(meteoswiss_source_fixture):
    # Stallikon suits the Uetliberg best, but measured no snow on these days, so Fluntern is next
    uetliberg_days = _find_meteoswiss_manual_site_daily_measurements(
        meteoswiss_source_fixture, UETLIBERG, date(2026, 9, 23), date(2026, 9, 24)
    )
    assert uetliberg_days[0].station.abbr == "SMA"
    assert uetliberg_days[0].station.is_manual_site


def test_find_meteoswiss_manual_site_daily_measurements_skips_a_site_that_measured_later(source_fixture, meteoswiss_source_fixture):
    # Stallikon measures the snow since 1998, so for 1975 its files are not downloaded
    uetliberg_days = _find_meteoswiss_manual_site_daily_measurements(
        meteoswiss_source_fixture, UETLIBERG, date(1975, 7, 1), date(1975, 7, 1)
    )
    assert uetliberg_days[0].station.abbr == "SMA"
    assert count_downloads(source_fixture, "ogd-nime_sko_d_historical.csv") == 0


def test_find_meteoswiss_manual_site_daily_measurements_without_a_site_close_enough(meteoswiss_source_fixture):
    # The Matterhorn is far from every manual site of the fakes
    matterhorn = UETLIBERG._replace(name="Matterhorn", altitude_m=4478.0, east_m=2617049.0, north_m=1091673.0)
    with pytest.raises(CannotAnswerError, match=r"No snow station is close enough to Matterhorn \(4478 m\)"):
        _find_meteoswiss_manual_site_daily_measurements(meteoswiss_source_fixture, matterhorn, date(2026, 9, 23), date(2026, 9, 23))


def test_find_meteoswiss_manual_site_daily_measurements_before_any_snow_measurement(meteoswiss_source_fixture):
    with pytest.raises(CannotAnswerError, match="No MeteoSwiss observer measured the snow depth on 1930-01-01."):
        _find_meteoswiss_manual_site_daily_measurements(meteoswiss_source_fixture, ZURICH, date(1930, 1, 1), date(1930, 1, 1))


def test_find_meteoswiss_manual_site_daily_measurements_without_snow_values(meteoswiss_source_fixture):
    # The sites close to the Uetliberg have no rows on 2026-09-21, which MeteoSwiss has published
    with pytest.raises(CannotAnswerError) as raised:
        _find_meteoswiss_manual_site_daily_measurements(meteoswiss_source_fixture, UETLIBERG, date(2026, 9, 21), date(2026, 9, 21))
    assert str(raised.value) == "None of the 2 snow stations tried for Uetliberg (869 m) has snow values for 2026-09-21."


# --- build_history_answer ---

def test_build_history_answer_without_the_manual_sites(mocker, meteoswiss_source_fixture, measurement_tools_now_fixture):
    # The weather stays when the manual site table cannot be downloaded
    mocker.patch.object(
        meteoswiss_source_fixture, "list_manual_sites", side_effect=requests.ConnectionError("connection refused")
    )
    answer = build_history_answer(meteoswiss_source_fixture, ZURICH, date(2026, 9, 23), 1)
    assert answer["weather"]["days"][0]["date"] == "2026-09-23"
    assert answer["snow"] is None


def test_build_history_answer_when_a_manual_site_cannot_be_downloaded(
    mocker, meteoswiss_source_fixture, measurement_tools_now_fixture
):
    # Fluntern's weather downloads, but its manual site's files do not, so the answer holds only the weather
    read_daily = meteoswiss_source_fixture.read_station_daily_measurements

    def read_daily_except_the_manual_site(station, first_day, last_day):
        if station.is_manual_site:
            raise requests.ConnectionError("connection refused")
        return read_daily(station, first_day, last_day)

    mocker.patch.object(
        meteoswiss_source_fixture, "read_station_daily_measurements", side_effect=read_daily_except_the_manual_site
    )
    answer = build_history_answer(meteoswiss_source_fixture, ZURICH, date(2026, 9, 23), 1)
    assert answer["weather"]["days"][0]["date"] == "2026-09-23"
    assert answer["snow"] is None


def test_build_history_answer_only_the_snow_without_the_manual_sites(
    mocker, meteoswiss_source_fixture, measurement_tools_now_fixture
):
    # The server tells the model it could not download the data
    mocker.patch.object(
        meteoswiss_source_fixture, "list_manual_sites", side_effect=requests.ConnectionError("connection refused")
    )
    with pytest.raises(requests.ConnectionError):
        build_history_answer(meteoswiss_source_fixture, ZURICH, date(2026, 9, 23), 1, "snow")


def test_build_history_answer_only_the_snow_of_a_day_not_published_yet(mocker, meteoswiss_source_fixture):
    # A day later, at 14:05 UTC, but MeteoSwiss has not added the row of 2026-09-25 to the recent files
    for module in ("history", "meteoswiss_source"):
        datetime_mock = mocker.patch(f"swiss_weather_mcp.measurements.{module}.datetime", wraps=datetime)
        datetime_mock.now.return_value = MEASUREMENTS_NOW + timedelta(days=1)
    with pytest.raises(CannotAnswerError) as raised:
        build_history_answer(meteoswiss_source_fixture, ZURICH, date(2026, 9, 25), 1, "snow")
    assert str(raised.value) == (
        "MeteoSwiss has not published the daily measurements for 2026-09-25 yet, try again later."
    )
