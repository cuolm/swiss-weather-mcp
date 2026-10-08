import os
import time

from fakes import build_place_names_archive
from swiss_weather_mcp.places.swisstopo_places import SwisstopoPlace, SwisstopoPlaceSource

# How far back a cached archive is dated to make the last look for a newer edition older than 30 days
OLD_CHECK_AGE_SECONDS = 31 * 24 * 60 * 60

NEWEST_ARCHIVE_NAME = "swissnames3d_2026_2056.csv.zip"
OLDER_ARCHIVE_NAME = "swissnames3d_2025_2056.csv.zip"


def count_requests(source_fixture, url_end: str) -> int:
    request_count = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith(url_end):
            request_count += 1
    return request_count


def write_cached_archive(tmp_path, archive_name: str, age_seconds: int):
    """Put an archive in the cache, last checked for a newer edition age_seconds ago."""
    archive = tmp_path / "locations" / archive_name
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(build_place_names_archive())
    checked_at = time.time() - age_seconds
    os.utime(archive, (checked_at, checked_at))
    return archive


# --- SwisstopoPlace ---

def test_place_display_name():
    oerlikon = SwisstopoPlace(
        name="Oerlikon", object_type="Quartier", altitude_m=444.0, east_m=2683618.0, north_m=1251448.0,
    )
    assert oerlikon.display_name == "Oerlikon (444 m)"


# --- load_places ---

def test_load_places_reads_only_the_chosen_object_types(swisstopo_place_source_fixture):
    # The field name and the railway station are left out, and Oerlikon is read from the points file only
    places = swisstopo_place_source_fixture.load_places()
    assert [(place.name, place.object_type) for place in places] == [
        ("Oerlikon", "Quartier"), ("Matterhorn", "Alpiner Gipfel"), ("Rheinfall", "Wasserfall"),
        ("Blausee", "Ort"), ("Blausee", "Quartier"), ("Uetliberg", "Huegel"), ("Uetliberg", "Ort"),
        ("Lindenhof", "Quartierteil"), ("Davos", "Ort"), ("Moos", "Ort"), ("Moos", "Ort"), ("Seeli", "Ort"), ("Seeli", "Ort"), ("Zürichsee", "See"), ("Blausee", "See"),
    ]


def test_load_places_reads_the_position(swisstopo_place_source_fixture):
    oerlikon = swisstopo_place_source_fixture.load_places()[0]
    assert (oerlikon.east_m, oerlikon.north_m, oerlikon.altitude_m) == (2683618.0, 1251448.0, 444.0)


def test_load_places_downloads_the_newest_edition(source_fixture, swisstopo_place_source_fixture, tmp_path):
    swisstopo_place_source_fixture.load_places()
    assert count_requests(source_fixture, NEWEST_ARCHIVE_NAME) == 1
    assert (tmp_path / "locations" / NEWEST_ARCHIVE_NAME).exists()


def test_load_places_reads_the_archive_once_per_process(source_fixture, swisstopo_place_source_fixture):
    swisstopo_place_source_fixture.load_places()
    swisstopo_place_source_fixture.load_places()
    assert count_requests(source_fixture, "swissnames3d/items") == 1


def test_load_places_uses_a_cached_archive(source_fixture, tmp_path):
    write_cached_archive(tmp_path, NEWEST_ARCHIVE_NAME, age_seconds=0)
    SwisstopoPlaceSource(tmp_path).load_places()
    assert count_requests(source_fixture, "swissnames3d/items") == 0
    assert count_requests(source_fixture, NEWEST_ARCHIVE_NAME) == 0


def test_load_places_keeps_the_archive_when_it_is_still_the_newest(source_fixture, tmp_path):
    # The last look for a newer edition is old, so the catalogue is asked, but nothing is downloaded
    archive = write_cached_archive(tmp_path, NEWEST_ARCHIVE_NAME, age_seconds=OLD_CHECK_AGE_SECONDS)
    SwisstopoPlaceSource(tmp_path).load_places()

    assert count_requests(source_fixture, "swissnames3d/items") == 1
    assert count_requests(source_fixture, NEWEST_ARCHIVE_NAME) == 0
    # The look is recorded, so the catalogue is not asked again for 30 days
    assert time.time() - archive.stat().st_mtime < OLD_CHECK_AGE_SECONDS


def test_load_places_replaces_an_older_edition(source_fixture, tmp_path):
    older_archive = write_cached_archive(tmp_path, OLDER_ARCHIVE_NAME, age_seconds=OLD_CHECK_AGE_SECONDS)
    SwisstopoPlaceSource(tmp_path).load_places()

    assert count_requests(source_fixture, NEWEST_ARCHIVE_NAME) == 1
    assert (tmp_path / "locations" / NEWEST_ARCHIVE_NAME).exists()
    assert not older_archive.exists()
