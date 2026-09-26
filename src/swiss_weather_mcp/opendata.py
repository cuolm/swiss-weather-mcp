"""
Download files from the MeteoSwiss Open Data portal and read its timestamps.

Shared by the forecast and the measurement code. All MeteoSwiss timestamps are UTC.
"""
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Optional
from zoneinfo import ZoneInfo

import requests

SWISS_TZ = ZoneInfo("Europe/Zurich")

REQUEST_TIMEOUT_SECONDS = 60
DOWNLOAD_CHUNK_SIZE_BYTES = 1024 * 1024


def parse_stamp(stamp_text: str) -> datetime:
    """Read a MeteoSwiss timestamp such as "202609231200", which is always UTC."""
    return datetime.strptime(stamp_text, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)


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
    Stream a file from MeteoSwiss to disk.

    Parameters:
        file_url (str): The file to download.
        target_file (Path): Where the finished file ends up.
        only_rows_starting_with (Optional[bytes]): Keep only the rows that start with this, or every
            line when None.
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
