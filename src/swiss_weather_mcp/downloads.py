"""
Download files and cache them on disk: from data.geo.admin.ch, the Swiss open data portal that
publishes the MeteoSwiss and the swisstopo data, and from the SLF measurement and bulletin APIs. Read
the cached MeteoSwiss CSV files and SLF JSON answers.

Shared by the forecast, the measurement and the places code.
"""
import csv
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_SIZE_BYTES = 1024 * 1024


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


def ensure_recent_file(file_url: str, target_file: Path, max_age: timedelta) -> Path:
    """Return a cached file, downloading it again when it is missing or older than max_age."""
    if target_file.exists():
        modified_at = datetime.fromtimestamp(target_file.stat().st_mtime, timezone.utc)
        if datetime.now(timezone.utc) - modified_at < max_age:
            return target_file

    logger.info(f"Downloading {file_url}")
    download_file(file_url, target_file)
    return target_file


def read_recent_csv_rows(file_url: str, target_file: Path, max_age: timedelta) -> List[Dict[str, str]]:
    """Return the rows of a cached MeteoSwiss CSV file, downloaded again when missing or older than max_age."""
    recent_file = ensure_recent_file(file_url, target_file, max_age)
    # MeteoSwiss writes its files in Latin-1, with ";" between the columns
    with open(recent_file, newline="", encoding="latin-1") as file:
        return list(csv.DictReader(file, delimiter=";"))


def read_recent_json(file_url: str, target_file: Path, max_age: timedelta) -> Any:
    """Return the content of a cached JSON answer, downloaded again when missing or older than max_age."""
    recent_file = ensure_recent_file(file_url, target_file, max_age)
    with open(recent_file, encoding="utf-8") as file:
        return json.load(file)
