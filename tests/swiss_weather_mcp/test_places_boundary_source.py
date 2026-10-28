import os
import time

from fakes import BOUNDARY_EDITION_IDS, build_boundaries_geopackage, build_square_geometry
from swiss_weather_mcp.places.boundary_source import Area, SwisstopoBoundarySource, _polygons_contain, _read_polygons

# How far back a cached GeoPackage is dated to make the last look for a newer edition older than 30 days
OLD_CHECK_AGE_SECONDS = 31 * 24 * 60 * 60

OLDER_EDITION_ID = BOUNDARY_EDITION_IDS[0]
NEWEST_EDITION_ID = BOUNDARY_EDITION_IDS[1]
# A square with a square hole, as a list of polygons, each its outer ring followed by its holes
SQUARE_WITH_HOLE = [[
    [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
    [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0), (4.0, 4.0)],
]]


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


# --- _polygons_contain ---

def test_polygons_contain_a_position_inside():
    assert _polygons_contain(SQUARE_WITH_HOLE, 2.0, 2.0)


def test_polygons_contain_not_a_position_in_a_hole():
    assert not _polygons_contain(SQUARE_WITH_HOLE, 5.0, 5.0)


def test_polygons_contain_not_a_position_outside():
    assert not _polygons_contain(SQUARE_WITH_HOLE, 12.0, 5.0)


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


# --- find_area ---

def test_find_area(boundary_source_fixture):
    boundary_source_fixture.load_geopackage()
    # Lindenhof, in the square of Zürich
    assert boundary_source_fixture.find_area(2683200.0, 1247500.0) == Area("Zürich", "Gemeindegebiet", "Zürich")


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


def test_find_area_before_loading(source_fixture, boundary_source_fixture):
    assert boundary_source_fixture.find_area(2683200.0, 1247500.0) is None
    assert count_requests(source_fixture, ".gpkg.zip") == 0
