import threading
import time
from datetime import datetime, timezone

import pytest

from fakes import EARLIER_RUN_ID, RUN_ID, FakeResponse, build_stac_item
from swiss_weather_mcp.forecast import parameters
from swiss_weather_mcp.forecast.source import LocalForecastSource


# --- find_latest_run ---

def test_find_latest_run_newest(mocker, tmp_path):
    item = build_stac_item(EARLIER_RUN_ID, parameters.ALL_PARAMETERS)
    item["assets"].update(build_stac_item(RUN_ID, parameters.ALL_PARAMETERS)["assets"])
    mocker.patch("swiss_weather_mcp.forecast.source.requests.get", return_value=FakeResponse(payload=item))

    run_id, _ = LocalForecastSource(tmp_path).find_latest_run()
    assert run_id == RUN_ID


def test_find_latest_run_falls_back_to_yesterday(mocker, tmp_path):
    # The item for a new day exists before its first run lands, and reports no assets at all
    responses = [FakeResponse(payload={"assets": {}}), FakeResponse(payload=build_stac_item(RUN_ID, parameters.ALL_PARAMETERS))]
    mocker.patch("swiss_weather_mcp.forecast.source.requests.get", side_effect=responses)

    run_id, _ = LocalForecastSource(tmp_path).find_latest_run()
    assert run_id == RUN_ID


def test_find_latest_run_skips_a_run_being_uploaded(mocker, tmp_path):
    # MeteoSwiss uploads the files of a run one by one, so the newest run can list only a few
    item = build_stac_item(EARLIER_RUN_ID, parameters.ALL_PARAMETERS)
    item["assets"].update(build_stac_item(RUN_ID, ["zprfr0hs"])["assets"])
    mocker.patch("swiss_weather_mcp.forecast.source.requests.get", return_value=FakeResponse(payload=item))

    run_id, _ = LocalForecastSource(tmp_path).find_latest_run()
    assert run_id == EARLIER_RUN_ID


def test_find_latest_run_skips_a_first_run_being_uploaded(mocker, tmp_path):
    # While the 00:00 run is being uploaded, the newest complete run is the 23:00 run in the day before
    responses = [
        FakeResponse(payload=build_stac_item("202609230000", ["zprfr0hs"])),
        FakeResponse(payload=build_stac_item("202609222300", parameters.ALL_PARAMETERS)),
    ]
    mocker.patch("swiss_weather_mcp.forecast.source.requests.get", side_effect=responses)

    run_id, _ = LocalForecastSource(tmp_path).find_latest_run()
    assert run_id == "202609222300"


def test_find_latest_run_uses_the_utc_day(mocker, tmp_path):
    # 22:30 UTC on 23 September is already 24 September in Switzerland
    datetime_mock = mocker.patch("swiss_weather_mcp.forecast.source.datetime", wraps=datetime)
    datetime_mock.now.return_value = datetime(2026, 9, 23, 22, 30, tzinfo=timezone.utc)
    get_mock = mocker.patch(
        "swiss_weather_mcp.forecast.source.requests.get",
        return_value=FakeResponse(payload=build_stac_item(RUN_ID, parameters.ALL_PARAMETERS)),
    )

    LocalForecastSource(tmp_path).find_latest_run()
    requested_url = get_mock.call_args_list[0].args[0]
    assert requested_url.endswith("/items/20260923-ch")


def test_find_latest_run_once_for_parallel_reads(mocker, tmp_path):
    # The reads of one tool run at the same time; a slow catalogue gives them time to overlap
    catalogue_delay_seconds = 0.2
    parallel_reads = 3

    def answer_slowly(url, **kwargs):
        time.sleep(catalogue_delay_seconds)
        return FakeResponse(payload=build_stac_item(RUN_ID, parameters.ALL_PARAMETERS))

    get_mock = mocker.patch("swiss_weather_mcp.forecast.source.requests.get", side_effect=answer_slowly)
    forecast_source = LocalForecastSource(tmp_path)
    threads = [threading.Thread(target=forecast_source.find_latest_run) for _ in range(parallel_reads)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert get_mock.call_count == 1


# --- read_series ---

def test_read_series_keeps_the_first_row(source_fixture, location_finder_fixture):
    # A point extract carries no header line, so skipping one would lose the first forecast hour
    series = source_fixture.read_series("tre200h0", location_finder_fixture.find_point("Zürich"))
    assert len(series.values) == 3
    assert series.values[datetime(2026, 9, 23, 6, tzinfo=timezone.utc)] == 6.0


def test_read_series_only_the_requested_point(source_fixture, location_finder_fixture):
    series = source_fixture.read_series("tre200h0", location_finder_fixture.find_point("Zürich"))
    assert -1 not in series.values.values()


def test_read_series_run_time(source_fixture, location_finder_fixture):
    series = source_fixture.read_series("tre200h0", location_finder_fixture.find_point("Zürich"))
    assert series.run_time == datetime(2026, 9, 22, 13, tzinfo=timezone.utc)


def test_read_series_downloads_once(source_fixture, location_finder_fixture):
    point = location_finder_fixture.find_point("Zürich")
    first_series = source_fixture.read_series("tre200h0", point)
    downloads = source_fixture.get_mock.call_count

    assert source_fixture.read_series("tre200h0", point).values == first_series.values
    assert source_fixture.get_mock.call_count == downloads


def test_read_series_stores_point_rows(source_fixture, tmp_path, location_finder_fixture):
    source_fixture.read_series("tre200h0", location_finder_fixture.find_point("Zürich"))
    stored = list((tmp_path / "runs" / RUN_ID).iterdir())
    assert [path.name for path in stored] == ["tre200h0_800100_2.csv"]
    assert b"999999" not in stored[0].read_bytes()


def test_read_series_stores_all_locations(tmp_path, source_fixture, location_finder_fixture):
    source_fixture.cache_all_locations = True
    source_fixture.read_series("tre200h0", location_finder_fixture.find_point("Zürich"))

    stored = tmp_path / "runs" / RUN_ID / "tre200h0.csv"
    assert stored.exists()
    assert b"999999" in stored.read_bytes()


def test_read_series_from_a_full_file(source_fixture, tmp_path, location_finder_fixture):
    point = location_finder_fixture.find_point("Zürich")
    from_extract = source_fixture.read_series("tre200h0", point).values

    source_fixture.cache_all_locations = True
    (tmp_path / "runs" / RUN_ID / "tre200h0_800100_2.csv").unlink()
    assert source_fixture.read_series("tre200h0", point).values == from_extract


def test_read_series_drops_old_runs(source_fixture, tmp_path, location_finder_fixture):
    for run in ("202609221100", EARLIER_RUN_ID):
        superseded = tmp_path / "runs" / run
        superseded.mkdir(parents=True)
        (superseded / "tre200h0_800100_2.csv").write_bytes(b"stale")

    source_fixture.read_series("tre200h0", location_finder_fixture.find_point("Zürich"))
    # 12:00 stays, another server process may still be reading it until it sees the 13:00 run
    assert sorted(path.name for path in (tmp_path / "runs").iterdir()) == [EARLIER_RUN_ID, RUN_ID]


def test_read_series_keeps_a_newer_run(source_fixture, tmp_path, location_finder_fixture):
    # Another server process on the same cache already moved on to a later run
    newer = tmp_path / "runs" / "202609221400"
    newer.mkdir(parents=True)
    (newer / "tre200h0_800100_2.csv").write_bytes(b"newer")

    source_fixture.read_series("tre200h0", location_finder_fixture.find_point("Zürich"))
    assert sorted(path.name for path in (tmp_path / "runs").iterdir()) == [RUN_ID, "202609221400"]


def test_read_series_leaves_no_partial_file(source_fixture, tmp_path, location_finder_fixture):
    source_fixture.read_series("tre200h0", location_finder_fixture.find_point("Zürich"))
    assert list(tmp_path.rglob("*.tmp")) == []


def test_read_series_no_values(source_fixture, location_finder_fixture):
    davos = location_finder_fixture.find_point("Davos")
    # Regional entries are published for some parameters and not others
    with pytest.raises(ValueError, match="no 'tre200h0' values for Davos"):
        source_fixture.read_series("tre200h0", davos)
