import os
import time

from fakes import build_place_names_archive
from swiss_weather_mcp.places.source import SwisstopoPlace, SwisstopoPlaceSource

# How far back a cached archive is dated to make the last look for a newer edition older than 30 days
OLD_CHECK_AGE_SECONDS = 31 * 24 * 60 * 60

NEWEST_ARCHIVE_NAME = "swissnames3d_2026_2056.csv.zip"
OLDER_ARCHIVE_NAME = "swissnames3d_2025_2056.csv.zip"

BLAUSEE = SwisstopoPlace(
    name="Blausee", object_types=("Quartier", "See"), population_band=1, is_exonym=False,
    altitude_m=887.0, east_m=2617276.0, north_m=1153561.0,
)


def count_requests(source_fixture, url_end: str) -> int:
    request_count = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith(url_end):
            request_count += 1
    return request_count


def write_cached_archive(tmp_path, archive_name: str, age_seconds: int):
    """Put an archive in the cache, last checked for a newer edition age_seconds ago."""
    archive = tmp_path / "places" / archive_name
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(build_place_names_archive())
    checked_at = time.time() - age_seconds
    os.utime(archive, (checked_at, checked_at))
    return archive


# --- SwisstopoPlace ---

def test_place_display_name():
    assert BLAUSEE.display_name == "Blausee (887 m)"


def test_place_description_names_every_type():
    assert BLAUSEE.description == "district and lake, fewer than 20 people, 887 m"


def test_place_description_without_people():
    peak = BLAUSEE._replace(
        name="Säntis", object_types=("Alpiner Gipfel", "Uebrige Bahnen", "Aussichtspunkt"), population_band=0,
        altitude_m=2502.0,
    )
    assert peak.description == "peak, cable car station and viewpoint, 2502 m"


# --- read_rows ---

def test_read_rows_reads_only_the_chosen_object_types(place_source_fixture):
    # The field name Rüti is left out, and Oerlikon is read from the points file only
    names = [place.name for place in place_source_fixture.read_rows()]
    assert "Rüti" not in names
    assert names.count("Oerlikon") == 1
    assert {"Zürich HB", "Matterhorn", "Zürichsee", "Küsnacht Im Holletsmoos 300/50/25m"} <= set(names)


def test_read_rows_reads_a_row(place_source_fixture):
    oerlikon = place_source_fixture.read_rows()[0]
    assert oerlikon == SwisstopoPlace(
        name="Oerlikon", object_types=("Quartier",), population_band=7, is_exonym=False,
        altitude_m=444.0, east_m=2683618.0, north_m=1251448.0,
    )


def test_read_rows_marks_an_exonym(place_source_fixture):
    zurich_rows = [place for place in place_source_fixture.read_rows() if place.name in ("Zürich", "Zurich")]
    assert [(place.name, place.is_exonym, place.population_band) for place in zurich_rows] == [
        ("Zürich", False, 9), ("Zurich", True, 9),
    ]


def test_read_rows_downloads_the_newest_edition(source_fixture, place_source_fixture, tmp_path):
    place_source_fixture.read_rows()
    assert count_requests(source_fixture, NEWEST_ARCHIVE_NAME) == 1
    assert (tmp_path / "places" / NEWEST_ARCHIVE_NAME).exists()


def test_read_rows_uses_a_cached_archive(source_fixture, tmp_path):
    write_cached_archive(tmp_path, NEWEST_ARCHIVE_NAME, age_seconds=0)
    SwisstopoPlaceSource(tmp_path).read_rows()
    assert count_requests(source_fixture, "swissnames3d/items") == 0
    assert count_requests(source_fixture, NEWEST_ARCHIVE_NAME) == 0


def test_read_rows_keeps_the_archive_when_it_is_still_the_newest(source_fixture, tmp_path):
    # The last look for a newer edition is old, so the catalogue is asked, but nothing is downloaded
    archive = write_cached_archive(tmp_path, NEWEST_ARCHIVE_NAME, age_seconds=OLD_CHECK_AGE_SECONDS)
    SwisstopoPlaceSource(tmp_path).read_rows()

    assert count_requests(source_fixture, "swissnames3d/items") == 1
    assert count_requests(source_fixture, NEWEST_ARCHIVE_NAME) == 0
    # The look is recorded, so the catalogue is not asked again for 30 days
    assert time.time() - archive.stat().st_mtime < OLD_CHECK_AGE_SECONDS


def test_read_rows_replaces_an_older_edition(source_fixture, tmp_path):
    older_archive = write_cached_archive(tmp_path, OLDER_ARCHIVE_NAME, age_seconds=OLD_CHECK_AGE_SECONDS)
    SwisstopoPlaceSource(tmp_path).read_rows()

    assert count_requests(source_fixture, NEWEST_ARCHIVE_NAME) == 1
    assert (tmp_path / "places" / NEWEST_ARCHIVE_NAME).exists()
    assert not older_archive.exists()
