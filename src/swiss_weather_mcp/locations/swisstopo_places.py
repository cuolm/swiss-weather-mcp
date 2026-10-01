"""
Read the named places of Switzerland from swissNAMES3D, the swisstopo collection of geographical
names, and find one by its name.

What swisstopo publishes:
- One edition of swissNAMES3D a year, as a zip archive of about 33 MB. It holds three CSV
  files with about 490,000 rows: the names of points (PKT), of areas (PLY) and of lines (LIN).
- Each row has a name, an object type, such as "Ort" for a place or "See" for a lake, and
  a position in LV95, the Swiss grid in metres, with its altitude.
- More than half of the rows are field names ("Flurname swisstopo"), the names of meadows,
  forests and slopes. The same field name is used all over the country.
- A name is not unique. Four peaks are called Piz Alv, and Blausee is a place, a quarter
  and five lakes.
- A place with names in several languages has one row for each name, such as Genève and Genf.
- A settlement is in the points file and again in the areas file.

    swissNAMES3D_PKT.csv  (names of points, 15 columns, the server reads 5)
    ┌────────────────────────────────────────────────────────────────┐
    │ UUID;NAME;...;OBJEKTART;...;E;N;Z                              │  header
    │ {...};Oerlikon;...;Quartier;...;2683618;1251448;444            │  a city district at 444 m
    │ {...};Matterhorn;...;Alpiner Gipfel;...;2617049;1091673;4478   │
    │ {...};Rüti;...;Flurname swisstopo;...                          │  a field name, not read
    │ ...                                                            │  about 390,000 rows
    └────────────────────────────────────────────────────────────────┘

How we use it:
1. Ask the catalogue for the newest edition and download its archive, when no archive is
   cached or the last look for a newer edition is more than 30 days ago. The archive is
   kept as swisstopo publishes it, so reading other object types needs no new download.
2. Read the object types in OBJECT_TYPES_BY_FILE straight from the archive, each type from one
   file only, so a settlement is not read twice. Keep them in memory for as long as the
   process runs.
3. Group the places that share a name and lie within 2 km of each other: they are one
   place, such as a hill and the hamlet on it, and the widest settlement stands for the
   group. A name gives one place per group, so the seven Blausee give six places.

    <cache dir>/locations/
    └── swissnames3d_2026_2056.csv.zip   (the newest edition, replaced when a newer one appears)
"""
import csv
import io
import logging
import threading
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, NamedTuple

import requests

from ..opendata import REQUEST_TIMEOUT_SECONDS, download_file
from .names import calculate_distance_m, normalise_location

logger = logging.getLogger(__name__)

EDITIONS_URL = "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissnames3d/items"
# Each edition is published in several formats; this one holds the CSV files in LV95
ARCHIVE_SUFFIX = "_2056.csv.zip"
EDITION_CHECK_INTERVAL = timedelta(days=30)

# The object types the server reads, by the file it reads them from
OBJECT_TYPES_BY_FILE = {
    "swissNAMES3D_PKT.csv": {
        "Ort", "Ortsteil", "Quartier", "Quartierteil",                       # places, their parts and city districts
        "Alpiner Gipfel", "Hauptgipfel", "Gipfel", "Haupthuegel", "Huegel",  # peaks and hills
        "Pass", "Strassenpass",                                              # passes
        "Wasserfall",                                                        # waterfalls
    },
    "swissNAMES3D_PLY.csv": {"See", "Gletscher", "Tal"},                     # lakes, glaciers and valleys
}
# How an answer calls each object type
OBJECT_TYPE_NAMES = {
    "Ort": "place", "Ortsteil": "part of a place", "Quartier": "district", "Quartierteil": "part of a district",
    "Alpiner Gipfel": "peak", "Hauptgipfel": "peak", "Gipfel": "peak", "Haupthuegel": "hill", "Huegel": "hill",
    "Pass": "pass", "Strassenpass": "pass", "Wasserfall": "waterfall",
    "See": "lake", "Gletscher": "glacier", "Tal": "valley",
}
# The object types of settlements, from the widest to the narrowest
SETTLEMENT_OBJECT_TYPES = ("Ort", "Ortsteil", "Quartier", "Quartierteil")
# Places with the same name this close together are one place
SAME_PLACE_RADIUS_M = 2000


class SwisstopoPlace(NamedTuple):
    """A named place as swissNAMES3D describes it."""
    name: str
    object_type: str  # such as "Ort" for a place or "See" for a lake
    altitude_m: float
    east_m: float   # LV95, the Swiss grid in metres
    north_m: float  # LV95

    @property
    def display_name(self) -> str:
        """The name shown to a reader, with the altitude, such as "Oerlikon (444 m)"."""
        return f"{self.name} ({self.altitude_m:.0f} m)"

    @property
    def object_type_name(self) -> str:
        """The object type in English, such as "lake" for "See"."""
        return OBJECT_TYPE_NAMES[self.object_type]


class SwisstopoPlaceSource:
    """Read the places of the newest swissNAMES3D edition, cached on disk as its archive."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "locations"
        self._places: List[SwisstopoPlace] = []

    def _fetch_newest_archive_url(self) -> str:
        """Ask the catalogue for the CSV archive of the newest edition."""
        response = requests.get(EDITIONS_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        editions = response.json()["features"]
        # Edition IDs end in their year, such as swissnames3d_2026, so the newest one is the largest string
        newest_edition = max(editions, key=lambda edition: edition["id"])
        for asset_name, asset in newest_edition["assets"].items():
            if asset_name.endswith(ARCHIVE_SUFFIX):
                return asset["href"]
        raise RuntimeError(f"The swissNAMES3D edition {newest_edition['id']} holds no CSV archive")

    def _ensure_archive(self) -> Path:
        """
        Return the cached archive. Look for a newer edition first when no archive is cached, or
        when the last look is more than EDITION_CHECK_INTERVAL ago.
        """
        # Usually one archive is cached. Two are left when the process stopped after downloading a new
        # edition but before deleting the old one. Archive names end in their year, so the newest is the last.
        cached_archives = sorted(self.cache_dir.glob(f"*{ARCHIVE_SUFFIX}"))
        if cached_archives:
            newest_cached_archive = cached_archives[-1]
            checked_at = datetime.fromtimestamp(newest_cached_archive.stat().st_mtime, timezone.utc)
            if datetime.now(timezone.utc) - checked_at < EDITION_CHECK_INTERVAL:
                return newest_cached_archive

        archive_url = self._fetch_newest_archive_url()
        archive = self.cache_dir / archive_url.rsplit("/", 1)[-1]
        if archive.exists():
            # Still the newest edition; its file time records this look
            archive.touch()
        else:
            logger.info(f"Downloading {archive_url}")
            download_file(archive_url, archive)

        for cached_archive in cached_archives:
            if cached_archive != archive:
                logger.info(f"Deleting the old swissNAMES3D edition {cached_archive.name}")
                cached_archive.unlink(missing_ok=True)
        return archive

    def load_places(self) -> List[SwisstopoPlace]:
        """Read the places of the object types in OBJECT_TYPES_BY_FILE into memory once per process."""
        if self._places:
            return self._places

        archive_file = self._ensure_archive()
        places = []
        with zipfile.ZipFile(archive_file) as archive:
            for file_name, object_types in OBJECT_TYPES_BY_FILE.items():
                with archive.open(file_name) as binary_file:
                    # The files start with a byte order mark, which utf-8-sig leaves out of the first column name
                    text_file = io.TextIOWrapper(binary_file, encoding="utf-8-sig", newline="")
                    for row in csv.DictReader(text_file, delimiter=";"):
                        if row["OBJEKTART"] not in object_types:
                            continue
                        place = SwisstopoPlace(
                            name=row["NAME"],
                            object_type=row["OBJEKTART"],
                            altitude_m=float(row["Z"]),
                            east_m=float(row["E"]),
                            north_m=float(row["N"]),
                        )
                        places.append(place)
        # Published only once complete, so a parallel request never sees part of the places
        self._places = places
        logger.info(f"Loaded {len(places)} places of swissNAMES3D")
        return places


def _group_close_places(places: List[SwisstopoPlace]) -> List[List[SwisstopoPlace]]:
    """Group places that share a name: a place joins every group with a member within SAME_PLACE_RADIUS_M."""
    groups: List[List[SwisstopoPlace]] = []
    for place in places:
        touching_groups = []
        for group in groups:
            for member in group:
                if calculate_distance_m(place, member) <= SAME_PLACE_RADIUS_M:
                    touching_groups.append(group)
                    break
        merged_group = [place]
        for group in touching_groups:
            merged_group.extend(group)
            groups.remove(group)
        groups.append(merged_group)
    return groups


def _choose_place(group: List[SwisstopoPlace]) -> SwisstopoPlace:
    """Return the widest settlement of a group of places with the same name, or its first place."""
    for object_type in SETTLEMENT_OBJECT_TYPES:
        for place in group:
            if place.object_type == object_type:
                return place
    return group[0]


class SwisstopoPlaceLookup:
    """Find the swisstopo places with a name."""

    def __init__(self, place_source: SwisstopoPlaceSource):
        self.place_source = place_source
        self._place_groups_by_name: Dict[str, List[List[SwisstopoPlace]]] = {}
        # The load at server start and a request may come at the same time, and only one may download
        self._loading_lock = threading.Lock()

    def load_place_groups(self) -> None:
        """Read the places and group them by name into memory once per process."""
        with self._loading_lock:
            if self._place_groups_by_name:
                return

            places = self.place_source.load_places()
            places_by_name: Dict[str, List[SwisstopoPlace]] = defaultdict(list)
            for place in places:
                places_by_name[normalise_location(place.name)].append(place)
            place_groups_by_name = {}
            for name, named_places in places_by_name.items():
                place_groups_by_name[name] = _group_close_places(named_places)
            self._place_groups_by_name = place_groups_by_name

    def find_places(self, name: str) -> List[SwisstopoPlace]:
        """Find the places with a name, one for each group of places within 2 km; none for an unknown name."""
        self.load_place_groups()
        groups = self._place_groups_by_name.get(normalise_location(name), [])
        places = []
        for group in groups:
            places.append(_choose_place(group))
        return places
