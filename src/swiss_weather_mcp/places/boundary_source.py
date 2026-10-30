"""
Find the municipality and canton of a position with swissBOUNDARIES3D, the swisstopo boundaries of the
administrative units of Switzerland and Liechtenstein.

What swisstopo publishes:
- Editions of swissBOUNDARIES3D, each in several formats. The GeoPackage of an edition is a zip archive
  of about 37 MB with one file of about 74 MB, a SQLite database of geodata.
- Its table tlm_hoheitsgebiet has an area for each municipality, with its name, kind and canton
  number, in LV95, the Swiss grid in metres. A few areas are of another kind: 11 belong to a canton
  itself, such as the Thunersee, and 2 are named after two municipalities, such as "Comunanza
  Capriasca/Lugano". The municipalities of Liechtenstein, Büsingen am Hochrhein and Campione d'Italia
  have no canton number.
- Its table tlm_kantonsgebiet has the 26 cantons, with their names in the local language, such as
  "Genève" or "Graubünden", and their canton numbers.
- Each table has a spatial index, an R-tree of the box around each area.

    tlm_hoheitsgebiet  (2136 areas, the server reads 5 of 25 columns)
    ┌─────────────────────────────────────────────────────────────────────────────────────┐
    │ id    name       objektart       kantonsnummer  geom                                │  columns
    │ …     Vals       Gemeindegebiet  18             (the area as a GeoPackage geometry) │  a municipality
    │ …     Thunersee  Kantonsgebiet   2              (…)                                 │  an area of a canton
    │ …     Vaduz      Gemeindegebiet                 (…)                                 │  in Liechtenstein
    └─────────────────────────────────────────────────────────────────────────────────────┘

How we use it:
1. Ask the catalogue for the newest edition and download its GeoPackage, when none is cached or the last
   look for a newer edition is more than 30 days ago, and unpack it.
2. For a position, ask the spatial index for the areas whose box holds it, and read only their shapes
   to find the one the position lies in. All shapes would take about 245 MB of memory.

    <cache dir>/boundaries/
    └── swissboundaries3d_2026-01.gpkg   (the newest edition, replaced when a newer one appears)
"""
import logging
import shutil
import sqlite3
import struct
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, NamedTuple, Optional, Tuple

import requests

from ..opendata import REQUEST_TIMEOUT_SECONDS, download_file

logger = logging.getLogger(__name__)

EDITIONS_URL = "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissboundaries3d/items"
# Each edition is published in several formats; this one holds the GeoPackage in LV95
ARCHIVE_SUFFIX = "_2056_5728.gpkg.zip"
GEOPACKAGE_SUFFIX = ".gpkg"
EDITION_CHECK_INTERVAL = timedelta(days=30)

# The kinds of area, in the column objektart
MUNICIPALITY_KIND = "Gemeindegebiet"
CANTON_AREA_KIND = "Kantonsgebiet"
SHARED_AREA_KIND = "Kommunanz"

# The areas whose box holds a position, with the name of their canton when they have one
AREAS_AROUND_QUERY = """
    SELECT area.name, area.objektart, canton.name, area.geom
    FROM tlm_hoheitsgebiet AS area
    JOIN rtree_tlm_hoheitsgebiet_geom AS box ON box.id = area.id
    LEFT JOIN tlm_kantonsgebiet AS canton ON canton.kantonsnummer = area.kantonsnummer
    WHERE box.minx <= ? AND box.maxx >= ? AND box.miny <= ? AND box.maxy >= ?
"""

# A GeoPackage geometry is a header, then the shape as well-known binary (WKB). The header has 8 bytes,
# then an envelope whose size bits 1 to 3 of the flags byte give.
GEOPACKAGE_HEADER_BYTES = 8
GEOPACKAGE_FLAGS_BYTE = 3
ENVELOPE_INDICATOR_TO_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
# A WKB shape starts with its byte order (1 byte) and type (4 bytes); counts take 4 bytes, values 8
WKB_SHAPE_HEADER_BYTES = 5
WKB_COUNT_BYTES = 4
WKB_VALUE_BYTES = 8
WKB_LITTLE_ENDIAN = 1
WKB_POLYGON = 3
WKB_MULTIPOLYGON = 6
# A WKB type above 1000, or with this flag, has a height (z) for each point, which is skipped
WKB_THREE_DIMENSIONS_FROM = 1000
WKB_Z_FLAG = 0x80000000

# A ring is a list of (east, north) in metres; a polygon is its outer ring followed by its holes
Ring = List[Tuple[float, float]]
Polygon = List[Ring]


class Area(NamedTuple):
    """The area a position lies in, as swissBOUNDARIES3D names it."""
    name: str  # such as "Vals", "Thunersee" or "Comunanza Capriasca/Lugano"
    kind: str  # MUNICIPALITY_KIND, CANTON_AREA_KIND or SHARED_AREA_KIND
    canton: Optional[str]  # in the local language, such as "Graubünden"; None outside the cantons

    @property
    def description(self) -> str:
        """
        Where a place lies, such as "municipality Vals, canton Graubünden".

        An area of a canton gives only "canton Bern", and outside the cantons only "municipality Vaduz".
        """
        if self.kind == CANTON_AREA_KIND:
            return f"canton {self.canton}"
        if self.kind == SHARED_AREA_KIND:
            # Its name is the word for it followed by the two municipalities, such as "Comunanza Capriasca/Lugano"
            municipality_names = self.name.split(" ", 1)[1]
            location = f"municipalities {municipality_names}"
        else:
            location = f"municipality {self.name}"
        if self.canton is None:
            return location
        return f"{location}, canton {self.canton}"


def _read_wkb(wkb: memoryview, offset: int) -> Tuple[List[Polygon], int]:
    """Read the polygons of the WKB shape at offset, and return them with the offset after it."""
    byte_order = "<" if wkb[offset] == WKB_LITTLE_ENDIAN else ">"
    (wkb_type,) = struct.unpack_from(byte_order + "I", wkb, offset + 1)
    has_heights = wkb_type >= WKB_THREE_DIMENSIONS_FROM or bool(wkb_type & WKB_Z_FLAG)
    shape_type = (wkb_type & ~WKB_Z_FLAG) % WKB_THREE_DIMENSIONS_FROM
    offset += WKB_SHAPE_HEADER_BYTES

    if shape_type == WKB_MULTIPOLYGON:
        (polygon_count,) = struct.unpack_from(byte_order + "I", wkb, offset)
        offset += WKB_COUNT_BYTES
        polygons = []
        for _ in range(polygon_count):
            member_polygons, offset = _read_wkb(wkb, offset)
            polygons.extend(member_polygons)
        return polygons, offset

    if shape_type != WKB_POLYGON:
        raise ValueError(f"A swissBOUNDARIES3D area has the WKB type {wkb_type}, not a polygon")

    values_per_point = 3 if has_heights else 2
    (ring_count,) = struct.unpack_from(byte_order + "I", wkb, offset)
    offset += WKB_COUNT_BYTES
    polygon = []
    for _ in range(ring_count):
        (point_count,) = struct.unpack_from(byte_order + "I", wkb, offset)
        offset += WKB_COUNT_BYTES
        value_count = point_count * values_per_point
        values = struct.unpack_from(f"{byte_order}{value_count}d", wkb, offset)
        offset += WKB_VALUE_BYTES * value_count
        east_values_m = values[0::values_per_point]
        north_values_m = values[1::values_per_point]
        ring = list(zip(east_values_m, north_values_m))
        polygon.append(ring)
    return [polygon], offset


def _read_polygons(geometry: bytes) -> List[Polygon]:
    """Read the polygons of a GeoPackage geometry, a polygon or a multipolygon."""
    flags = geometry[GEOPACKAGE_FLAGS_BYTE]
    envelope_indicator = (flags >> 1) & 0b111
    envelope_bytes = ENVELOPE_INDICATOR_TO_BYTES[envelope_indicator]
    wkb_start = GEOPACKAGE_HEADER_BYTES + envelope_bytes
    wkb = memoryview(geometry)[wkb_start:]
    polygons, _ = _read_wkb(wkb, 0)
    return polygons


def _ring_contains(ring: Ring, east_m: float, north_m: float) -> bool:
    """Tell whether a position lies inside a closed ring, by counting the ring's edges a line to the east crosses."""
    is_inside = False
    # Each edge runs from one corner to the next; a closed ring ends at its first corner
    edges = zip(ring, ring[1:])
    for (first_east_m, first_north_m), (second_east_m, second_north_m) in edges:
        crosses_the_line_north = (first_north_m > north_m) != (second_north_m > north_m)
        if not crosses_the_line_north:
            continue
        share_of_edge = (north_m - first_north_m) / (second_north_m - first_north_m)
        crossing_east_m = first_east_m + share_of_edge * (second_east_m - first_east_m)
        if east_m < crossing_east_m:
            is_inside = not is_inside
    return is_inside


def _polygons_contain(polygons: List[Polygon], east_m: float, north_m: float) -> bool:
    """Tell whether a position lies inside one of the polygons, inside its outer ring and outside its holes."""
    for outer_ring, *holes in polygons:
        if not _ring_contains(outer_ring, east_m, north_m):
            continue
        is_in_a_hole = any(_ring_contains(hole, east_m, north_m) for hole in holes)
        if not is_in_a_hole:
            return True
    return False


class SwisstopoBoundarySource:
    """Find the area of a position in the newest swissBOUNDARIES3D edition, cached on disk."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "boundaries"
        self._geopackage_file: Optional[Path] = None

    def _fetch_newest_archive_url(self) -> str:
        """Ask the catalogue for the GeoPackage archive of the newest edition."""
        response = requests.get(EDITIONS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        editions = response.json()["features"]
        # Edition IDs end in their year and month, such as swissboundaries3d_2026-01, so the newest one
        # is the largest string
        newest_edition = max(editions, key=lambda edition: edition["id"])
        for asset_name, asset in newest_edition["assets"].items():
            if asset_name.endswith(ARCHIVE_SUFFIX):
                return asset["href"]
        raise RuntimeError(f"The swissBOUNDARIES3D edition {newest_edition['id']} holds no GeoPackage")

    def _download_geopackage(self, archive_url: str, geopackage_file: Path) -> None:
        """Download the archive of an edition, unpack its GeoPackage to geopackage_file and delete the archive."""
        archive_name = archive_url.rsplit("/", 1)[-1]
        archive_file = self.cache_dir / archive_name
        # A unique name, moved into place only when complete, so an interrupted unpack is never taken for a
        # cached file, as in download_file
        partial_name = f"{geopackage_file.name}.{uuid.uuid4().hex}.tmp"
        partial_file = geopackage_file.with_name(partial_name)
        logger.info(f"Downloading {archive_url}")

        try:
            download_file(archive_url, archive_file)
            with zipfile.ZipFile(archive_file) as archive:
                geopackage_names = [name for name in archive.namelist() if name.endswith(GEOPACKAGE_SUFFIX)]
                with archive.open(geopackage_names[0]) as packed, open(partial_file, "wb") as unpacked:
                    shutil.copyfileobj(packed, unpacked)
            partial_file.replace(geopackage_file)
        finally:
            archive_file.unlink(missing_ok=True)
            partial_file.unlink(missing_ok=True)

    def _ensure_geopackage(self) -> Path:
        """
        Return the cached GeoPackage.

        Look for a newer edition first when none is cached, or when the last look is more than
        EDITION_CHECK_INTERVAL ago.
        """
        # Usually one GeoPackage is cached. Two are left when the process stopped after downloading a new
        # edition but before deleting the old one. GeoPackage names end in their year and month, so the
        # newest is the last.
        found_files = self.cache_dir.glob(f"*{GEOPACKAGE_SUFFIX}")
        cached_files = sorted(found_files)
        if cached_files:
            newest_cached_file = cached_files[-1]
            modified_at_seconds = newest_cached_file.stat().st_mtime
            checked_at = datetime.fromtimestamp(modified_at_seconds, timezone.utc)
            if datetime.now(timezone.utc) - checked_at < EDITION_CHECK_INTERVAL:
                return newest_cached_file

        archive_url = self._fetch_newest_archive_url()
        archive_name = archive_url.rsplit("/", 1)[-1]
        edition_name = archive_name.removesuffix(ARCHIVE_SUFFIX)
        geopackage_file = self.cache_dir / f"{edition_name}{GEOPACKAGE_SUFFIX}"
        if geopackage_file.exists():
            # Still the newest edition; its file time records this look
            geopackage_file.touch()
        else:
            self._download_geopackage(archive_url, geopackage_file)

        for cached_file in cached_files:
            if cached_file != geopackage_file:
                logger.info(f"Deleting the old swissBOUNDARIES3D edition {cached_file.name}")
                cached_file.unlink(missing_ok=True)
        return geopackage_file

    def load_geopackage(self) -> None:
        """Make sure the newest GeoPackage is cached, so find_area can use it."""
        self._geopackage_file = self._ensure_geopackage()
        logger.info(f"Loaded the swissBOUNDARIES3D edition {self._geopackage_file.name}")

    def find_area(self, east_m: float, north_m: float) -> Optional[Area]:
        """
        Find the area an LV95 position lies in.

        Return None outside every area, and before load_geopackage has cached the GeoPackage.
        """
        if self._geopackage_file is None:
            return None

        # A connection of its own for each call, as requests come from several threads
        database_uri = f"file:{self._geopackage_file}?mode=ro"
        connection = sqlite3.connect(database_uri, uri=True)
        try:
            cursor = connection.execute(AREAS_AROUND_QUERY, (east_m, east_m, north_m, north_m))
            areas = cursor.fetchall()
        finally:
            connection.close()

        for area_name, area_kind, canton_name, geometry in areas:
            polygons = _read_polygons(geometry)
            if _polygons_contain(polygons, east_m, north_m):
                return Area(area_name, area_kind, canton_name)
        return None
