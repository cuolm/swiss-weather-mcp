"""
Download files and cache them on disk: from data.geo.admin.ch, the Swiss open data portal that
publishes the MeteoSwiss and the swisstopo data, and from the SLF measurement and bulletin APIs. Read
the cached MeteoSwiss CSV files and SLF JSON answers. Keep the newest edition of a swisstopo dataset.

Shared by the forecast, the measurement and the places code.
"""
import csv
import json
import logging
import threading
import uuid
import weakref
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_SIZE_BYTES = 1024 * 1024
SWISSTOPO_EDITION_CHECK_INTERVAL = timedelta(days=30)


# One lock per file being downloaded, so parallel requests download a missing file only once. The locks
# are kept weakly: a lock disappears as soon as no thread holds it or waits for it, so this never grows.
_path_to_download_lock: "weakref.WeakValueDictionary[Path, threading.Lock]" = weakref.WeakValueDictionary()
_download_locks_guard = threading.Lock()


def _find_or_create_download_lock(target_file: Path) -> threading.Lock:
    """Return the lock all threads share for a file, creating it when none exists."""
    with _download_locks_guard:
        download_lock = _path_to_download_lock.get(target_file)
        if download_lock is None:
            download_lock = threading.Lock()
            _path_to_download_lock[target_file] = download_lock
        return download_lock


def _write_rows_starting_with(response: requests.Response, row_prefix: bytes, file: BinaryIO) -> None:
    """Write the rows that start with a prefix from a streamed response to a file."""
    # Chunks are split by hand, iter_lines() takes about 45 seconds for the million lines of a file
    remainder = b""
    for chunk in response.iter_content(DOWNLOAD_CHUNK_SIZE_BYTES):
        lines = (remainder + chunk).split(b"\n")
        remainder = lines.pop()  # the last piece may be half a line
        for line in lines:
            if line.startswith(row_prefix):
                file.write(line + b"\n")
    if remainder.startswith(row_prefix):
        file.write(remainder + b"\n")


def download_file(file_url: str, target_file: Path, only_rows_starting_with: Optional[bytes] = None) -> None:
    """
    Stream a file to disk. With only_rows_starting_with, keep only the rows that start with it.
    """
    target_file.parent.mkdir(parents=True, exist_ok=True)
    # A unique name, moved into place only when complete, so an interrupted download is never
    # taken for a cached file, and two requests for the same file do not write into each other
    partial_file = target_file.with_name(f"{target_file.name}.{uuid.uuid4().hex}.tmp")
    try:
        with requests.get(file_url, stream=True, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response.raise_for_status()
            with open(partial_file, "wb") as file:
                if only_rows_starting_with is None:
                    for chunk in response.iter_content(DOWNLOAD_CHUNK_SIZE_BYTES):
                        file.write(chunk)
                else:
                    _write_rows_starting_with(response, only_rows_starting_with, file)
        partial_file.replace(target_file)
    finally:
        partial_file.unlink(missing_ok=True)


def _is_recent(target_file: Path, max_age: timedelta) -> bool:
    """Tell whether a cached file exists and is younger than max_age."""
    if not target_file.exists():
        return False
    modified_at = datetime.fromtimestamp(target_file.stat().st_mtime, timezone.utc)
    age = datetime.now(timezone.utc) - modified_at
    return age < max_age


def ensure_recent_file(
    file_url: str, target_file: Path, max_age: timedelta, serve_stale_on_error: bool = False
) -> Path:
    """
    Return a cached file, downloading it again when it is missing or older than max_age.

    With serve_stale_on_error, return the older copy when the download fails, for files whose older copy
    is still correct.
    """
    with _find_or_create_download_lock(target_file):
        # Another request may have downloaded the file while this one waited for the lock
        if _is_recent(target_file, max_age):
            return target_file

        logger.info(f"Downloading {file_url}")
        try:
            download_file(file_url, target_file)
        except requests.RequestException as error:
            if serve_stale_on_error and target_file.exists():
                logger.warning(f"Could not download {file_url}, using the older copy: {error}")
                return target_file
            raise
    return target_file


def ensure_file(file_url: str, target_file: Path, only_rows_starting_with: Optional[bytes] = None) -> Path:
    """Return a cached file, downloading it only when it is missing, for files that never change."""
    with _find_or_create_download_lock(target_file):
        # Another request may have downloaded the file while this one waited for the lock
        if target_file.exists():
            return target_file

        logger.info(f"Downloading {file_url}")
        download_file(file_url, target_file, only_rows_starting_with=only_rows_starting_with)
    return target_file


def read_recent_csv_rows(
    file_url: str, target_file: Path, max_age: timedelta, serve_stale_on_error: bool = False
) -> List[Dict[str, str]]:
    """Return the rows of a cached MeteoSwiss CSV file, downloaded again when missing or older than max_age."""
    recent_file = ensure_recent_file(file_url, target_file, max_age, serve_stale_on_error)
    # MeteoSwiss writes its files in Latin-1, with ";" between the columns
    with open(recent_file, newline="", encoding="latin-1") as file:
        return list(csv.DictReader(file, delimiter=";"))


def read_recent_json(file_url: str, target_file: Path, max_age: timedelta, serve_stale_on_error: bool = False) -> Any:
    """Return the content of a cached JSON answer, downloaded again when missing or older than max_age."""
    recent_file = ensure_recent_file(file_url, target_file, max_age, serve_stale_on_error)
    with open(recent_file, encoding="utf-8") as file:
        return json.load(file)


def _fetch_newest_swisstopo_archive_url(editions_url: str, archive_suffix: str) -> str:
    """Ask a swisstopo catalogue for the archive of the newest edition, the one whose name ends in archive_suffix."""
    response = requests.get(editions_url, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    editions = response.json()["features"]
    # Edition IDs end in their date, such as swissnames3d_2026 or swissboundaries3d_2026-01, so the newest
    # one is the largest string
    newest_edition = max(editions, key=lambda edition: edition["id"])
    for asset_name, asset in newest_edition["assets"].items():
        if asset_name.endswith(archive_suffix):
            return asset["href"]
    raise RuntimeError(f"The edition {newest_edition['id']} holds no file ending in {archive_suffix}")


def ensure_newest_swisstopo_edition(
    editions_url: str,
    archive_suffix: str,
    cache_dir: Path,
    cached_suffix: str,
    download: Callable[[str, Path], None],
) -> Path:
    """
    Return the cached file of the newest edition of a swisstopo dataset.

    Look for a newer edition first when no file is cached, or when the last look is more than
    SWISSTOPO_EDITION_CHECK_INTERVAL ago. The cached file is named after the archive, with
    archive_suffix replaced by cached_suffix; download(archive_url, cached_file) writes it.
    """
    # Usually one file is cached. Two are left when the process stopped after downloading a new edition
    # but before deleting the old one. File names end in their date, so the newest is the last.
    found_files = cache_dir.glob(f"*{cached_suffix}")
    cached_files = sorted(found_files)
    if cached_files and _is_recent(cached_files[-1], SWISSTOPO_EDITION_CHECK_INTERVAL):
        return cached_files[-1]

    archive_url = _fetch_newest_swisstopo_archive_url(editions_url, archive_suffix)
    archive_name = archive_url.rsplit("/", 1)[-1]
    edition_name = archive_name.removesuffix(archive_suffix)
    cached_file = cache_dir / f"{edition_name}{cached_suffix}"
    if cached_file.exists():
        # Still the newest edition; its file time records this look
        cached_file.touch()
    else:
        logger.info(f"Downloading {archive_url}")
        download(archive_url, cached_file)

    for old_file in cached_files:
        if old_file != cached_file:
            logger.info(f"Deleting the old edition {old_file.name}")
            old_file.unlink(missing_ok=True)
    return cached_file
