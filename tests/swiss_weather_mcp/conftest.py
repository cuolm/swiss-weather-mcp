from datetime import datetime

import pytest

from fakes import (
    ALL_STATIONS_LATEST_VALUES_CSV, MEASUREMENTS_NOW, RUN_ID, FakeResponse, build_parameter_csv,
    build_point_table_csv, build_stac_item, build_station_now_values_csv, build_station_table_csv,
)
from swiss_weather_mcp.forecast.service import ForecastService
from swiss_weather_mcp.forecast.source import ForecastSource
from swiss_weather_mcp.locations import LocationFinder
from swiss_weather_mcp.measurements.service import MeasurementService
from swiss_weather_mcp.measurements.source import MeasurementSource


@pytest.fixture
def source_fixture(mocker, tmp_path):
    """
    Return a ForecastSource backed by fake HTTP responses, plus the mock, so tests can count
    downloads and swap in different published data.
    """
    published = {
        "tre200h0": {"202609230600": "6.0", "202609231200": "12.0", "202609231300": "14.5"},
        "treq10h0": {"202609230600": "5.1", "202609231200": "10.8", "202609231300": "13.2"},
        "treq90h0": {"202609230600": "7.0", "202609231200": "13.4", "202609231300": "16.1"},
        "sre000h0": {"202609230600": "30", "202609230700": "0", "202609230800": "0", "202609230900": "0",
                     "202609231000": "0", "202609231100": "0", "202609231200": "60", "202609231300": "45"},
        "nprolohs": {"202609231200": "0.50"},
        "npromths": {"202609231200": "0.00"},
        "nprohihs": {"202609231200": "0.50"},
        "jww003i0": {"202609231200": "2", "202609231300": "3"},
        "fu3010h0": {"202609231200": "12.5"},
        "fu3010h1": {"202609231200": "38.0"},
        "fu3q90h1": {"202609231200": "55.0"},
        "dkl010h0": {"202609231200": "217"},
        "tre200px": {"202609230000": "20.6", "202609240000": "18.4"},
        # Published, but with no rows for any place in the point table
        "tre200pn": {}, "rka150p0": {}, "rreq10p0": {}, "rreq90p0": {}, "jp2000d0": {}, "zprfr0hs": {},
        # Two 3-hour blocks, 12:00 to 15:00 and 15:00 to 18:00 UTC
        "rp0003i0": {"202609231200": "10", "202609231300": "20", "202609231500": "40", "202609231800": "80"},
        "rre003i0": {"202609231500": "0.0", "202609231800": "2.4"},
        "rreq90h0": {"202609231300": "0.2", "202609231400": "1.1", "202609231500": "0.4",
                     "202609231600": "0.9", "202609231700": "3.5", "202609231800": "2.0"},
    }

    def fake_get(url, **kwargs):
        if url.endswith("meta_point.csv"):
            return FakeResponse(body=build_point_table_csv())
        if url.endswith("ogd-smn_meta_stations.csv"):
            return FakeResponse(body=build_station_table_csv())
        if url.endswith("VQHA80.csv"):
            return FakeResponse(body=ALL_STATIONS_LATEST_VALUES_CSV.encode("latin-1"))
        if url.endswith("_t_now.csv"):
            abbr = url.rsplit("/", 2)[-2]
            return FakeResponse(body=build_station_now_values_csv(abbr))
        if "/items/" in url:
            return FakeResponse(payload=build_stac_item(RUN_ID, published))
        parameter = url.rsplit("/", 1)[-1].removesuffix(".csv")
        return FakeResponse(body=build_parameter_csv(parameter, published[parameter]))

    get_mock = mocker.patch("swiss_weather_mcp.forecast.source.requests.get", side_effect=fake_get)
    forecast_source = ForecastSource(tmp_path)
    forecast_source.get_mock = get_mock
    forecast_source.published = published
    return forecast_source


@pytest.fixture
def location_finder_fixture(source_fixture, tmp_path):
    """Return a LocationFinder reading the fake point table, served by the same fake HTTP responses."""
    return LocationFinder(tmp_path)


@pytest.fixture
def service_fixture(location_finder_fixture, source_fixture):
    """Return a ForecastService reading from the fake data source."""
    return ForecastService(location_finder_fixture, source_fixture)


@pytest.fixture
def measurement_source_fixture(mocker, source_fixture, tmp_path):
    """
    Return a MeasurementSource reading the fake station files, served by the same fake HTTP responses,
    at a time when their latest rows are 5 minutes old.
    """
    datetime_mock = mocker.patch("swiss_weather_mcp.measurements.source.datetime", wraps=datetime)
    datetime_mock.now.return_value = MEASUREMENTS_NOW
    return MeasurementSource(tmp_path)


@pytest.fixture
def measurement_service_fixture(location_finder_fixture, measurement_source_fixture):
    """Return a MeasurementService reading from the fake station files."""
    return MeasurementService(location_finder_fixture, measurement_source_fixture)
