import os
import sys
import time
import zipfile

import pytest
import requests
from fakes import BOUNDARY_EDITION_IDS, build_boundaries_geopackage, build_square_geometry

from swiss_weather_mcp.places.boundary_source import Area, SwisstopoBoundarySource, _read_polygons


# How far back a cached GeoPackage is dated to make the last look for a newer edition older than 30 days
OLD_CHECK_AGE_SECONDS = 31 * 24 * 60 * 60

OLDER_EDITION_ID = BOUNDARY_EDITION_IDS[0]
NEWEST_EDITION_ID = BOUNDARY_EDITION_IDS[1]
# Windows forbids "?" in file names, so a cache folder there cannot hold one
URL_CHARACTERS_FOLDER = "with#hash%25" if sys.platform == "win32" else "with#hash?and%25"


def count_requests(source_fixture, url_end: str) -> int:
    request_count = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith(url_end):
            request_count += 1
    return request_count


def list_cached_names(tmp_path):
    """Return the names of the files in the boundaries cache, sorted."""
    cached_files = (tmp_path / "boundaries").iterdir()
    return sorted(cached_file.name for cached_file in cached_files)


def write_cached_geopackage(tmp_path, edition_id: str, age_seconds: int):
    """Put a GeoPackage in the cache, last checked for a newer edition age_seconds ago."""
    geopackage_file = tmp_path / "boundaries" / f"{edition_id}.gpkg"
    geopackage_file.parent.mkdir(parents=True, exist_ok=True)
    build_boundaries_geopackage(geopackage_file)
    checked_at = time.time() - age_seconds
    os.utime(geopackage_file, (checked_at, checked_at))
    return geopackage_file


def write_archive_without_geopackage(archive_url: str, archive_file) -> None:
    """Download an archive that holds no GeoPackage, only a text file."""
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_file, "w") as archive:
        archive.writestr("readme.txt", "no GeoPackage here")


# --- Area ---

def test_area_description_of_a_municipality():
    assert Area("Vals", "Gemeindegebiet", "Graubünden").description == "municipality Vals, canton Graubünden"


def test_area_description_without_canton():
    assert Area("Vaduz", "Gemeindegebiet", None).description == "municipality Vaduz"


def test_area_description_of_an_area_of_a_canton():
    assert Area("Thunersee", "Kantonsgebiet", "Bern").description == "canton Bern"


def test_area_description_of_a_shared_area():
    shared_area = Area("Comunanza Capriasca/Lugano", "Kommunanz", "Ticino")
    assert shared_area.description == "municipalities Capriasca/Lugano, canton Ticino"


# --- _read_polygons ---

def test_read_polygons_of_a_multipolygon_with_heights():
    geometry = build_square_geometry(1.0, 3.0, 2.0, 5.0)
    polygons = _read_polygons(geometry)
    assert polygons == [[[(1.0, 2.0), (3.0, 2.0), (3.0, 5.0), (1.0, 5.0), (1.0, 2.0)]]]


# --- load_geopackage ---

def test_load_geopackage_downloads_the_newest_edition(source_fixture, boundary_source_fixture, tmp_path):
    boundary_source_fixture.load_geopackage()
    assert count_requests(source_fixture, f"{NEWEST_EDITION_ID}_2056_5728.gpkg.zip") == 1
    # Only the unpacked GeoPackage is kept, not its archive
    assert list_cached_names(tmp_path) == [f"{NEWEST_EDITION_ID}.gpkg"]


def test_load_geopackage_uses_a_cached_geopackage(source_fixture, tmp_path):
    write_cached_geopackage(tmp_path, NEWEST_EDITION_ID, age_seconds=0)
    boundary_source = SwisstopoBoundarySource(tmp_path)
    boundary_source.load_geopackage()
    assert count_requests(source_fixture, "swissboundaries3d/items") == 0


def test_load_geopackage_keeps_the_geopackage_when_it_is_still_the_newest(source_fixture, tmp_path):
    cached_file = write_cached_geopackage(tmp_path, NEWEST_EDITION_ID, age_seconds=OLD_CHECK_AGE_SECONDS)
    boundary_source = SwisstopoBoundarySource(tmp_path)
    boundary_source.load_geopackage()

    assert count_requests(source_fixture, "swissboundaries3d/items") == 1
    assert count_requests(source_fixture, ".gpkg.zip") == 0
    # The look for a newer edition is recorded, so the next start trusts the cache again
    checked_ago_seconds = time.time() - cached_file.stat().st_mtime
    assert checked_ago_seconds < OLD_CHECK_AGE_SECONDS


def test_load_geopackage_replaces_an_older_edition(source_fixture, tmp_path):
    write_cached_geopackage(tmp_path, OLDER_EDITION_ID, age_seconds=OLD_CHECK_AGE_SECONDS)
    boundary_source = SwisstopoBoundarySource(tmp_path)
    boundary_source.load_geopackage()
    assert list_cached_names(tmp_path) == [f"{NEWEST_EDITION_ID}.gpkg"]


def test_load_geopackage_refuses_an_archive_without_a_geopackage(mocker, source_fixture, tmp_path):
    mocker.patch(
        "swiss_weather_mcp.places.boundary_source.download_file", side_effect=write_archive_without_geopackage
    )
    boundary_source = SwisstopoBoundarySource(tmp_path)
    with pytest.raises(ValueError, match="holds no GeoPackage"):
        boundary_source.load_geopackage()


def test_load_geopackage_keeps_the_cached_edition_when_the_archive_has_no_geopackage(
    mocker, source_fixture, tmp_path
):
    mocker.patch(
        "swiss_weather_mcp.places.boundary_source.download_file", side_effect=write_archive_without_geopackage
    )
    write_cached_geopackage(tmp_path, OLDER_EDITION_ID, age_seconds=OLD_CHECK_AGE_SECONDS)
    boundary_source = SwisstopoBoundarySource(tmp_path)
    boundary_source.load_geopackage()
    assert list_cached_names(tmp_path) == [f"{OLDER_EDITION_ID}.gpkg"]


# --- find_area ---

def test_find_area(boundary_source_fixture):
    boundary_source_fixture.load_geopackage()
    # Lindenhof, in the square of Zürich
    assert boundary_source_fixture.find_area(2683200.0, 1247500.0) == Area("Zürich", "Gemeindegebiet", "Zürich")


def test_find_area_in_a_cache_folder_with_url_characters(source_fixture, tmp_path):
    # SQLite reads the GeoPackage path as a URL, in which "#", "?" and "%" have their own meaning
    boundary_source = SwisstopoBoundarySource(tmp_path / URL_CHARACTERS_FOLDER)
    boundary_source.load_geopackage()
    assert boundary_source.find_area(2683200.0, 1247500.0) == Area("Zürich", "Gemeindegebiet", "Zürich")


def test_find_area_among_overlapping_boxes(boundary_source_fixture):
    boundary_source_fixture.load_geopackage()
    # The Blausee lake lies in the box of the Greifensee only
    assert boundary_source_fixture.find_area(2687500.0, 1252500.0) == Area("Greifensee", "Kantonsgebiet", "Zürich")


def test_find_area_without_canton(boundary_source_fixture):
    boundary_source_fixture.load_geopackage()
    assert boundary_source_fixture.find_area(2758000.0, 1222000.0) == Area("Vaduz", "Gemeindegebiet", None)


def test_find_area_outside_every_area(boundary_source_fixture):
    boundary_source_fixture.load_geopackage()
    assert boundary_source_fixture.find_area(2600000.0, 1200000.0) is None


def test_find_area_after_another_server_replaced_the_edition(source_fixture, tmp_path):
    # This server loaded the older edition; another server using the same cache then downloaded the newest
    # and deleted the older one
    older_file = write_cached_geopackage(tmp_path, OLDER_EDITION_ID, age_seconds=0)
    boundary_source = SwisstopoBoundarySource(tmp_path)
    boundary_source.load_geopackage()
    write_cached_geopackage(tmp_path, NEWEST_EDITION_ID, age_seconds=0)
    older_file.unlink()

    assert boundary_source.find_area(2683200.0, 1247500.0) == Area("Zürich", "Gemeindegebiet", "Zürich")
    municipalities = boundary_source.find_municipalities_named("Davos")
    assert [municipality.name for municipality in municipalities] == ["Davos"]


def test_find_area_waits_for_the_geopackage(source_fixture, boundary_source_fixture):
    assert boundary_source_fixture.find_area(2683200.0, 1247500.0) == Area("Zürich", "Gemeindegebiet", "Zürich")
    # Loaded once, not again for the next question
    boundary_source_fixture.find_area(2683200.0, 1247500.0)
    assert count_requests(source_fixture, ".gpkg.zip") == 1


# --- find_municipalities_named ---

def test_find_municipalities_named(boundary_source_fixture):
    boundary_source_fixture.load_geopackage()
    municipalities = boundary_source_fixture.find_municipalities_named("DAVOS")
    assert [municipality.name for municipality in municipalities] == ["Davos"]
    davos_square = [(2775000.0, 1180000.0), (2790000.0, 1180000.0), (2790000.0, 1192000.0), (2775000.0, 1192000.0)]
    assert municipalities[0].polygons == [[[*davos_square, davos_square[0]]]]


def test_find_municipalities_named_without_the_canton_and_accents(boundary_source_fixture):
    boundary_source_fixture.load_geopackage()
    municipalities = boundary_source_fixture.find_municipalities_named("Saint-Barthelemy")
    assert [municipality.name for municipality in municipalities] == ["Saint-Barthélemy (VD)"]


def test_find_municipalities_named_not_an_area_of_a_canton(boundary_source_fixture):
    boundary_source_fixture.load_geopackage()
    assert boundary_source_fixture.find_municipalities_named("Greifensee") == []


def test_find_municipalities_named_waits_for_the_geopackage(source_fixture, boundary_source_fixture):
    municipalities = boundary_source_fixture.find_municipalities_named("Davos")
    assert [municipality.name for municipality in municipalities] == ["Davos"]
    assert count_requests(source_fixture, ".gpkg.zip") == 1


def test_find_municipalities_named_after_a_failed_load(mocker, source_fixture, boundary_source_fixture):
    download_mock = mocker.patch(
        "swiss_weather_mcp.places.boundary_source.download_file", side_effect=requests.ConnectionError("offline")
    )
    with pytest.raises(requests.ConnectionError):
        boundary_source_fixture.load_geopackage()

    # The failed load at start is not remembered, so the next question downloads again
    mocker.stop(download_mock)
    municipalities = boundary_source_fixture.find_municipalities_named("Davos")
    assert [municipality.name for municipality in municipalities] == ["Davos"]
