import threading
import time
from datetime import datetime, timezone

import pytest
from fakes import EARLIER_RUN_ID, RUN_ID, FakeResponse, build_stac_item

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.forecast import parameters
from swiss_weather_mcp.forecast.source import ForecastPoint, ForecastSource
from swiss_weather_mcp.places.place_source import SwisstopoPlace


# Points of the fake point table
ZURICH_8001 = ForecastPoint("800100", "2", "Zürich", "8001", 409.0, 2683348.0, 1247414.0)
ZURICH_8002 = ForecastPoint("800200", "2", "Zürich", "8002", 431.0, 2682417.0, 1246175.0)
DAVOS = ForecastPoint("26", "1", "Davos", "", 1594.0, 2783519.0, 1187459.0)
BUCHS_AARAU = ForecastPoint("67", "1", "Buchs / Aarau", "", 387.0, 2648396.0, 1248370.0)

ZURICH = SwisstopoPlace("Zürich", ("Ort",), 9, False, 418.0, 2683500.0, 1247184.0)
MATTERHORN = SwisstopoPlace("Matterhorn", ("Alpiner Gipfel",), 0, False, 4478.0, 2617049.0, 1091673.0)


# --- find_latest_run ---

def test_find_latest_run_newest(mocker, tmp_path):
    item = build_stac_item(EARLIER_RUN_ID, parameters.ALL_PARAMETERS)
    item["assets"].update(build_stac_item(RUN_ID, parameters.ALL_PARAMETERS)["assets"])
    mocker.patch("requests.Session.get", return_value=FakeResponse(payload=item))

    run_id = ForecastSource(tmp_path).find_latest_run().run_id
    assert run_id == RUN_ID


def test_find_latest_run_falls_back_to_yesterday(mocker, tmp_path):
    # The item for a new day exists before its first run lands, and reports no assets at all
    responses = [
        FakeResponse(payload={"assets": {}}),
        FakeResponse(payload=build_stac_item(RUN_ID, parameters.ALL_PARAMETERS)),
    ]
    mocker.patch("requests.Session.get", side_effect=responses)

    run_id = ForecastSource(tmp_path).find_latest_run().run_id
    assert run_id == RUN_ID


def test_find_latest_run_without_a_complete_run(mocker, tmp_path):
    # Neither today's nor yesterday's item holds a run with every file
    responses = [FakeResponse(payload={"assets": {}}), FakeResponse(payload={"assets": {}})]
    mocker.patch("requests.Session.get", side_effect=responses)

    with pytest.raises(CannotAnswerError, match="no complete forecast for today or yesterday, try again later"):
        ForecastSource(tmp_path).find_latest_run()


def test_find_latest_run_skips_a_run_being_uploaded(mocker, tmp_path):
    # MeteoSwiss uploads the files of a run one by one, so the newest run can list only a few
    item = build_stac_item(EARLIER_RUN_ID, parameters.ALL_PARAMETERS)
    item["assets"].update(build_stac_item(RUN_ID, ["zprfr0hs"])["assets"])
    mocker.patch("requests.Session.get", return_value=FakeResponse(payload=item))

    run_id = ForecastSource(tmp_path).find_latest_run().run_id
    assert run_id == EARLIER_RUN_ID


def test_find_latest_run_skips_a_first_run_being_uploaded(mocker, tmp_path):
    # While the 00:00 run is being uploaded, the newest complete run is the 23:00 run in the day before
    responses = [
        FakeResponse(payload=build_stac_item("202609230000", ["zprfr0hs"])),
        FakeResponse(payload=build_stac_item("202609222300", parameters.ALL_PARAMETERS)),
    ]
    mocker.patch("requests.Session.get", side_effect=responses)

    run_id = ForecastSource(tmp_path).find_latest_run().run_id
    assert run_id == "202609222300"


def test_find_latest_run_uses_the_utc_day(mocker, tmp_path):
    # 22:30 UTC on 23 September is already 24 September in Switzerland
    datetime_mock = mocker.patch("swiss_weather_mcp.forecast.source.datetime", wraps=datetime)
    datetime_mock.now.return_value = datetime(2026, 9, 23, 22, 30, tzinfo=timezone.utc)
    get_mock = mocker.patch(
        "requests.Session.get",
        return_value=FakeResponse(payload=build_stac_item(RUN_ID, parameters.ALL_PARAMETERS)),
    )

    ForecastSource(tmp_path).find_latest_run()
    requested_url = get_mock.call_args_list[0].args[0]
    assert requested_url.endswith("/items/20260923-ch")


def test_find_latest_run_once_for_parallel_reads(mocker, tmp_path):
    # The reads of one tool run at the same time; a slow catalogue gives them time to overlap
    catalogue_delay_seconds = 0.2
    parallel_reads = 3

    def answer_slowly(url, **kwargs):
        time.sleep(catalogue_delay_seconds)
        return FakeResponse(payload=build_stac_item(RUN_ID, parameters.ALL_PARAMETERS))

    get_mock = mocker.patch("requests.Session.get", side_effect=answer_slowly)
    forecast_source = ForecastSource(tmp_path)
    threads = [threading.Thread(target=forecast_source.find_latest_run) for _ in range(parallel_reads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert get_mock.call_count == 1


# --- find_forecast_point ---

def test_find_forecast_point_nearby(source_fixture):
    # Zürich 8001 lies 0.3 km away and 9 m lower, Zürich 8002 1.5 km away and 13 m higher
    assert source_fixture.find_forecast_point(ZURICH) == ZURICH_8001


def test_find_forecast_point_weighs_the_height_gap(source_fixture):
    # Zürich 8001 lies 0.76 km away but 22 m lower, Zürich 8002 0.79 km away at the same height
    between_the_points = ZURICH._replace(altitude_m=431.0, east_m=2682900.0, north_m=1246800.0)
    assert source_fixture.find_forecast_point(between_the_points) == ZURICH_8002


def test_find_forecast_point_far_from_every_point(source_fixture):
    # No point of the fake table lies within 5 km of the Matterhorn, so the nearest one is taken
    assert source_fixture.find_forecast_point(MATTERHORN) == BUCHS_AARAU


def test_find_forecast_point_skips_a_regional_forecast(source_fixture):
    # The place stands on Flachland Deutschschweiz, which has only part of the parameters
    on_the_regional_forecast = ZURICH._replace(altitude_m=387.0, east_m=2648390.0, north_m=1248365.0)
    assert source_fixture.find_forecast_point(on_the_regional_forecast) == BUCHS_AARAU


def test_find_forecast_point_caches_the_point_table_in_its_folder(source_fixture, tmp_path):
    source_fixture.find_forecast_point(ZURICH)
    assert (tmp_path / "forecast" / "ogd-local-forecasting_meta_point.csv").exists()


# --- read_series ---

def test_read_series_keeps_the_first_row(source_fixture):
    # A point extract carries no header line, so skipping one would lose the first forecast hour
    run = source_fixture.find_latest_run()
    series = source_fixture.read_series("tre200h0", ZURICH_8001, run)
    assert len(series.time_to_value) == 3
    assert series.time_to_value[datetime(2026, 9, 23, 6, tzinfo=timezone.utc)] == 6.0


def test_read_series_only_the_requested_point(source_fixture):
    run = source_fixture.find_latest_run()
    series = source_fixture.read_series("tre200h0", ZURICH_8001, run)
    assert -1 not in series.time_to_value.values()


def test_read_series_run_time(source_fixture):
    run = source_fixture.find_latest_run()
    series = source_fixture.read_series("tre200h0", ZURICH_8001, run)
    assert series.run_time == datetime(2026, 9, 22, 13, tzinfo=timezone.utc)


def test_read_series_downloads_once(source_fixture):
    run = source_fixture.find_latest_run()
    point = ZURICH_8001
    first_series = source_fixture.read_series("tre200h0", point, run)
    downloads = source_fixture.get_mock.call_count

    assert source_fixture.read_series("tre200h0", point, run).time_to_value == first_series.time_to_value
    assert source_fixture.get_mock.call_count == downloads


def test_read_series_stores_point_rows(source_fixture, tmp_path):
    run = source_fixture.find_latest_run()
    source_fixture.read_series("tre200h0", ZURICH_8001, run)
    stored = list((tmp_path / "forecast" / "runs" / RUN_ID).iterdir())
    assert [path.name for path in stored] == ["tre200h0_800100_2.csv"]
    assert b"999999" not in stored[0].read_bytes()


def test_read_series_stores_all_points(tmp_path, source_fixture):
    run = source_fixture.find_latest_run()
    source_fixture.cache_all_forecast_points = True
    source_fixture.read_series("tre200h0", ZURICH_8001, run)

    stored = tmp_path / "forecast" / "runs" / RUN_ID / "tre200h0.csv"
    assert stored.exists()
    assert b"999999" in stored.read_bytes()


def test_read_series_from_a_full_file(source_fixture, tmp_path):
    run = source_fixture.find_latest_run()
    point = ZURICH_8001
    from_extract = source_fixture.read_series("tre200h0", point, run).time_to_value

    source_fixture.cache_all_forecast_points = True
    (tmp_path / "forecast" / "runs" / RUN_ID / "tre200h0_800100_2.csv").unlink()
    assert source_fixture.read_series("tre200h0", point, run).time_to_value == from_extract


def test_read_series_drops_old_runs(source_fixture, tmp_path):
    run = source_fixture.find_latest_run()
    for old_run_id in ("202609221100", EARLIER_RUN_ID):
        superseded = tmp_path / "forecast" / "runs" / old_run_id
        superseded.mkdir(parents=True)
        (superseded / "tre200h0_800100_2.csv").write_bytes(b"stale")

    source_fixture.read_series("tre200h0", ZURICH_8001, run)
    # 12:00 stays, another server process may still be reading it until it sees the 13:00 run
    assert sorted(path.name for path in (tmp_path / "forecast" / "runs").iterdir()) == [EARLIER_RUN_ID, RUN_ID]


def test_read_series_keeps_a_newer_run(source_fixture, tmp_path):
    # Another server process on the same cache already moved on to a later run
    run = source_fixture.find_latest_run()
    newer = tmp_path / "forecast" / "runs" / "202609221400"
    newer.mkdir(parents=True)
    (newer / "tre200h0_800100_2.csv").write_bytes(b"newer")

    source_fixture.read_series("tre200h0", ZURICH_8001, run)
    assert sorted(path.name for path in (tmp_path / "forecast" / "runs").iterdir()) == [RUN_ID, "202609221400"]


def test_read_series_leaves_no_partial_file(source_fixture, tmp_path):
    run = source_fixture.find_latest_run()
    source_fixture.read_series("tre200h0", ZURICH_8001, run)
    assert list(tmp_path.rglob("*.tmp")) == []


def test_read_series_no_values(source_fixture):
    run = source_fixture.find_latest_run()
    davos = DAVOS
    # Regional entries are published for some parameters and not others
    with pytest.raises(CannotAnswerError, match="no 'tre200h0' values for Davos"):
        source_fixture.read_series("tre200h0", davos, run)
