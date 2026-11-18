import os
import threading
import time
from datetime import timedelta

import pytest
import requests

from swiss_weather_mcp import downloads
from swiss_weather_mcp.downloads import ensure_file, ensure_recent_file


FILE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/ogd-smn_meta_stations.csv"
OTHER_FILE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/ogd-smn_meta_datainventory.csv"
MAX_AGE = timedelta(days=7)
# How far back a cached file is dated to make it older than the 7 days it is kept
OLD_FILE_AGE_SECONDS = 8 * 24 * 60 * 60
OLD_CONTENT = b"station_abbr;station_name\nGLA;Glarus\n"
NEW_CONTENT = b"station_abbr;station_name\nELM;Elm\n"
# Long enough for the other request to arrive while a download is running
SLOW_DOWNLOAD_SECONDS = 0.2
# A thread that is still running after this has hung
THREAD_TIMEOUT_SECONDS = 5


def run_in_parallel(*calls):
    """Run each call in its own thread at the same time; return the errors they raised."""
    errors = []

    def run_and_keep_error(call):
        try:
            call()
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=run_and_keep_error, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(THREAD_TIMEOUT_SECONDS)
        assert not thread.is_alive(), "a thread hung"
    return errors


@pytest.fixture
def failing_download(mocker):
    """Make every download fail, as when the server cannot be reached."""
    return mocker.patch(
        "swiss_weather_mcp.downloads.requests.get", side_effect=requests.ConnectionError("server down")
    )


@pytest.fixture
def old_file(tmp_path):
    """Return a cached file older than MAX_AGE."""
    cached_file = tmp_path / "ogd-smn_meta_stations.csv"
    cached_file.write_bytes(OLD_CONTENT)
    old_time = time.time() - OLD_FILE_AGE_SECONDS
    os.utime(cached_file, (old_time, old_time))
    return cached_file


def test_ensure_recent_file_serves_the_older_copy_when_the_download_fails(failing_download, old_file):
    recent_file = ensure_recent_file(FILE_URL, old_file, MAX_AGE, serve_stale_on_error=True)

    assert recent_file == old_file
    assert recent_file.read_bytes() == OLD_CONTENT
    assert failing_download.call_count == 1


def test_ensure_recent_file_raises_when_the_download_fails_without_serve_stale_on_error(failing_download, old_file):
    with pytest.raises(requests.ConnectionError):
        ensure_recent_file(FILE_URL, old_file, MAX_AGE)


def test_ensure_recent_file_raises_when_the_download_fails_and_there_is_no_copy(failing_download, tmp_path):
    missing_file = tmp_path / "ogd-smn_meta_stations.csv"

    with pytest.raises(requests.ConnectionError):
        ensure_recent_file(FILE_URL, missing_file, MAX_AGE, serve_stale_on_error=True)


def test_ensure_recent_file_cannot_reach_the_network_in_tests(tmp_path):
    # The block_network fixture stops a test without a fake, even where the stale copy would hide a failed download
    missing_file = tmp_path / "ogd-smn_meta_stations.csv"

    with pytest.raises(RuntimeError, match="must not use the network"):
        ensure_recent_file(FILE_URL, missing_file, MAX_AGE, serve_stale_on_error=True)


@pytest.fixture
def slow_download(mocker):
    """Make every download take a while and write NEW_CONTENT, as a real one would."""

    def download_slowly(file_url, target_file, only_rows_starting_with=None):
        time.sleep(SLOW_DOWNLOAD_SECONDS)
        target_file.write_bytes(NEW_CONTENT)

    return mocker.patch("swiss_weather_mcp.downloads.download_file", side_effect=download_slowly)


def test_ensure_recent_file_downloads_a_missing_file_once_for_parallel_requests(slow_download, tmp_path):
    # Two users ask for the same missing file at the same time; the second waits and uses the first's download
    missing_file = tmp_path / "ogd-smn_meta_stations.csv"

    errors = run_in_parallel(
        lambda: ensure_recent_file(FILE_URL, missing_file, MAX_AGE),
        lambda: ensure_recent_file(FILE_URL, missing_file, MAX_AGE),
    )

    assert errors == []
    assert slow_download.call_count == 1
    assert missing_file.read_bytes() == NEW_CONTENT


def test_ensure_recent_file_downloads_different_files_at_the_same_time(mocker, tmp_path):
    # Each download waits until the other one has started, so downloads one after the other would time out
    both_downloads_started = threading.Barrier(2, timeout=THREAD_TIMEOUT_SECONDS)

    def download_when_both_started(file_url, target_file, only_rows_starting_with=None):
        both_downloads_started.wait()
        target_file.write_bytes(NEW_CONTENT)

    mocker.patch("swiss_weather_mcp.downloads.download_file", side_effect=download_when_both_started)

    errors = run_in_parallel(
        lambda: ensure_recent_file(FILE_URL, tmp_path / "ogd-smn_meta_stations.csv", MAX_AGE),
        lambda: ensure_recent_file(OTHER_FILE_URL, tmp_path / "ogd-smn_meta_datainventory.csv", MAX_AGE),
    )

    assert errors == []


def test_ensure_recent_file_forgets_the_lock_of_a_finished_download(slow_download, tmp_path):
    missing_file = tmp_path / "ogd-smn_meta_stations.csv"

    run_in_parallel(
        lambda: ensure_recent_file(FILE_URL, missing_file, MAX_AGE),
        lambda: ensure_recent_file(FILE_URL, missing_file, MAX_AGE),
    )

    assert len(downloads._path_to_download_lock) == 0


def test_ensure_recent_file_tries_again_after_a_failed_download(mocker, tmp_path):
    # The first download fails; the request that waited for it downloads the file itself instead of hanging
    missing_file = tmp_path / "ogd-smn_meta_stations.csv"
    outcomes = [requests.ConnectionError("server down"), NEW_CONTENT]

    def fail_first_then_download(file_url, target_file, only_rows_starting_with=None):
        time.sleep(SLOW_DOWNLOAD_SECONDS)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        target_file.write_bytes(outcome)

    download_mock = mocker.patch("swiss_weather_mcp.downloads.download_file", side_effect=fail_first_then_download)

    errors = run_in_parallel(
        lambda: ensure_recent_file(FILE_URL, missing_file, MAX_AGE),
        lambda: ensure_recent_file(FILE_URL, missing_file, MAX_AGE),
    )

    assert len(errors) == 1
    assert isinstance(errors[0], requests.ConnectionError)
    assert download_mock.call_count == 2
    assert missing_file.read_bytes() == NEW_CONTENT


def test_ensure_file_downloads_a_missing_file_once_for_parallel_requests(slow_download, tmp_path):
    # A forecast file of one run, asked for by two users at the same time
    missing_file = tmp_path / "tre200h0_800100_2.csv"

    errors = run_in_parallel(
        lambda: ensure_file(FILE_URL, missing_file),
        lambda: ensure_file(FILE_URL, missing_file),
    )

    assert errors == []
    assert slow_download.call_count == 1
    assert missing_file.read_bytes() == NEW_CONTENT


def test_ensure_file_keeps_a_file_however_old(slow_download, old_file):
    # A forecast file never changes, so its age does not matter
    kept_file = ensure_file(FILE_URL, old_file)

    assert kept_file.read_bytes() == OLD_CONTENT
    assert slow_download.call_count == 0
