import io
import os
import threading
import time
from datetime import timedelta

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3 import HTTPResponse
from urllib3.exceptions import ReadTimeoutError

from swiss_weather_mcp import downloads
from swiss_weather_mcp.downloads import ensure_file, ensure_newest_swisstopo_edition, ensure_recent_file


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
EDITIONS_URL = "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissboundaries3d/items"
ARCHIVE_SUFFIX = "_2056_5728.gpkg.zip"
GEOPACKAGE_SUFFIX = ".gpkg"
NEWEST_EDITION_ID = "swissboundaries3d_2026-01"
NEWEST_ARCHIVE_NAME = f"{NEWEST_EDITION_ID}{ARCHIVE_SUFFIX}"
NEWEST_ARCHIVE_URL = f"https://data.geo.admin.ch/ch.swisstopo.swissboundaries3d/{NEWEST_EDITION_ID}/{NEWEST_ARCHIVE_NAME}"
NEWEST_CATALOGUE = {
    "features": [{"id": NEWEST_EDITION_ID, "assets": {NEWEST_ARCHIVE_NAME: {"href": NEWEST_ARCHIVE_URL}}}],
}
# How far back a cached edition is dated to make its last look for a newer edition older than 30 days
OLD_CHECK_AGE_SECONDS = 31 * 24 * 60 * 60
# Kept before the block_network fixture replaces it, for the tests of the retry inside requests and urllib3
REAL_ADAPTER_SEND = HTTPAdapter.send


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
    return mocker.patch("requests.Session.get", side_effect=requests.ConnectionError("server down"))


@pytest.fixture
def old_file(tmp_path):
    """Return a cached file older than MAX_AGE."""
    cached_file = tmp_path / "ogd-smn_meta_stations.csv"
    cached_file.write_bytes(OLD_CONTENT)
    old_time = time.time() - OLD_FILE_AGE_SECONDS
    os.utime(cached_file, (old_time, old_time))
    return cached_file


@pytest.fixture
def server_requests(mocker):
    """Return a fake of the request urllib3 sends to the server, with the real requests and urllib3 above it."""
    mocker.patch("requests.adapters.HTTPAdapter.send", REAL_ADAPTER_SEND)
    return mocker.patch("urllib3.connectionpool.HTTPConnectionPool._make_request")


def build_slow_handshake_error():
    """Return the error urllib3 raises when the TLS handshake takes longer than the connect timeout."""
    return ReadTimeoutError(None, FILE_URL, "Read timed out. (read timeout=10)")


def test_fetch_url_tries_again_after_a_slow_handshake(server_requests):
    answer = HTTPResponse(body=io.BytesIO(NEW_CONTENT), status=200, preload_content=False)
    server_requests.side_effect = [build_slow_handshake_error(), answer]

    response = downloads.fetch_url(FILE_URL)

    assert response.content == NEW_CONTENT
    assert server_requests.call_count == 2


def test_fetch_url_gives_up_after_the_second_try(server_requests):
    server_requests.side_effect = [build_slow_handshake_error(), build_slow_handshake_error()]

    with pytest.raises(requests.ConnectionError):
        downloads.fetch_url(FILE_URL)
    assert server_requests.call_count == 2


def test_ensure_recent_file_serves_the_older_copy_when_the_download_fails(failing_download, old_file):
    recent_file = ensure_recent_file(FILE_URL, old_file, MAX_AGE, serve_stale_on_error=True)

    assert recent_file == old_file
    assert recent_file.read_bytes() == OLD_CONTENT
    assert failing_download.call_count == 1
    # A silent server is given up after the short connect timeout, not after the long read timeout
    assert failing_download.call_args.kwargs["timeout"] == (10, 60)


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


@pytest.fixture
def old_geopackage(tmp_path):
    """Return the cached GeoPackage of an older edition, last looked at more than 30 days ago."""
    cached_file = tmp_path / "swissboundaries3d_2025-04.gpkg"
    cached_file.write_bytes(OLD_CONTENT)
    old_time = time.time() - OLD_CHECK_AGE_SECONDS
    os.utime(cached_file, (old_time, old_time))
    return cached_file


def test_ensure_newest_swisstopo_edition_keeps_the_cached_file_when_the_catalogue_cannot_be_reached(
    failing_download, old_geopackage, mocker
):
    download = mocker.Mock()

    newest_file = ensure_newest_swisstopo_edition(
        EDITIONS_URL, ARCHIVE_SUFFIX, old_geopackage.parent, GEOPACKAGE_SUFFIX, download
    )

    assert newest_file == old_geopackage
    assert newest_file.read_bytes() == OLD_CONTENT
    assert download.call_count == 0


def test_ensure_newest_swisstopo_edition_raises_when_the_catalogue_cannot_be_reached_and_nothing_is_cached(
    failing_download, tmp_path, mocker
):
    download = mocker.Mock()

    with pytest.raises(requests.ConnectionError):
        ensure_newest_swisstopo_edition(EDITIONS_URL, ARCHIVE_SUFFIX, tmp_path, GEOPACKAGE_SUFFIX, download)


def fake_catalogue(mocker, catalogue):
    """Make every request answer with the catalogue JSON."""
    response = mocker.Mock()
    response.json.return_value = catalogue
    mocker.patch("requests.Session.get", return_value=response)


@pytest.mark.parametrize(
    "catalogue",
    [
        {"type": "FeatureCollection"},
        {"features": []},
        {"features": [{"id": NEWEST_EDITION_ID, "assets": {}}]},
    ],
    ids=["no features", "no editions", "no matching archive"],
)
def test_ensure_newest_swisstopo_edition_keeps_the_cached_file_when_the_catalogue_has_an_unknown_shape(
    catalogue, old_geopackage, mocker
):
    fake_catalogue(mocker, catalogue)
    download = mocker.Mock()

    newest_file = ensure_newest_swisstopo_edition(
        EDITIONS_URL, ARCHIVE_SUFFIX, old_geopackage.parent, GEOPACKAGE_SUFFIX, download
    )

    assert newest_file == old_geopackage
    assert download.call_count == 0


def test_ensure_newest_swisstopo_edition_keeps_the_cached_file_when_the_download_fails(old_geopackage, mocker):
    fake_catalogue(mocker, NEWEST_CATALOGUE)
    download = mocker.Mock(side_effect=requests.ConnectionError("server down"))

    newest_file = ensure_newest_swisstopo_edition(
        EDITIONS_URL, ARCHIVE_SUFFIX, old_geopackage.parent, GEOPACKAGE_SUFFIX, download
    )

    assert newest_file == old_geopackage
    assert newest_file.read_bytes() == OLD_CONTENT
    assert download.call_count == 1
