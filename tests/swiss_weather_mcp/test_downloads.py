import os
import time
from datetime import timedelta

import pytest
import requests

from swiss_weather_mcp.downloads import ensure_recent_file

FILE_URL = "https://data.geo.admin.ch/ch.meteoschweiz.ogd-smn/ogd-smn_meta_stations.csv"
MAX_AGE = timedelta(days=7)
# How far back a cached file is dated to make it older than the 7 days it is kept
OLD_FILE_AGE_SECONDS = 8 * 24 * 60 * 60
OLD_CONTENT = b"station_abbr;station_name\nGLA;Glarus\n"


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
