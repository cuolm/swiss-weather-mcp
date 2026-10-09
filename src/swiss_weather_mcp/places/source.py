"""
Read the geographic names of swissNAMES3D, the swisstopo collection of the names on the national map.

What swisstopo publishes:
- One edition of swissNAMES3D a year, as a zip archive of about 33 MB. It holds three CSV
  files with about 500,000 rows: the names of points (PKT), of areas (PLY) and of lines (LIN).
- Each row is one geographic name: the name, an object type, such as "Ort" for a settlement
  or "See" for a lake, the number of people for a settlement, given as a band such as
  "2'000 bis 9'999", and a position in LV95, the Swiss grid in metres, with its altitude.
- A place has its official name, in the language spoken there. Only the big towns and some
  mountains and lakes also have names in other languages, such as Genf for Genève. Each such
  name is a row of its own, an exonym.
- More than half of the rows are field names ("Flurname swisstopo"), the names of meadows,
  forests and slopes. The same field name is used all over the country.
- A settlement is in the points file and again in the areas file.

    swissNAMES3D_PKT.csv  (names of points, 15 columns, the server reads 7)
    ┌──────────────────────────────────────────────────────────────────────────────┐
    │ UUID;NAME;...;NAMEN_TYP;...;OBJEKTART;...;EINWOHNERKATEGORIE;E;N;Z           │  header
    │ {...};Zürich;...;Endonym;...;Ort;...;> 100'000;2683500;1247184;418           │  a city
    │ {...};Zurich;...;Exonym;...;Ort;...;> 100'000;2683500;1247184;418            │  its French name
    │ {...};Matterhorn;...;einfacher Name;...;Alpiner Gipfel;...;k_W;...;4478      │  a peak, no people
    │ {...};Rüti;...;einfacher Name;...;Flurname swisstopo;...                     │  a field name, not read
    │ ...                                                                          │  about 390,000 rows
    └──────────────────────────────────────────────────────────────────────────────┘

How we use it:
1. Ask the catalogue for the newest edition and download its archive, when no archive is
   cached or the last look for a newer edition is more than 30 days ago. The archive is
   kept as swisstopo publishes it, so reading other object types needs no new download.
2. Read the object types in OBJECT_TYPES_BY_FILE straight from the archive, each type from
   one file only, so a settlement is not read twice. Each row becomes one SwisstopoPlace.

    <cache dir>/places/
    └── swissnames3d_2026_2056.csv.zip   (the newest edition, replaced when a newer one appears)
"""
import csv
import io
import logging
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, NamedTuple, Tuple

import requests

from ..opendata import REQUEST_TIMEOUT_SECONDS, download_file

logger = logging.getLogger(__name__)

EDITIONS_URL = "https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissnames3d/items"
# Each edition is published in several formats; this one holds the CSV files in LV95
ARCHIVE_SUFFIX = "_2056.csv.zip"
EDITION_CHECK_INTERVAL = timedelta(days=30)

# The object types the server reads, by the file it reads them from
OBJECT_TYPES_BY_FILE = {
    "swissNAMES3D_PKT.csv": {
        "Ort", "Ortsteil", "Quartier", "Quartierteil",                                   # settlements
        "Alpiner Gipfel", "Hauptgipfel", "Gipfel", "Haupthuegel", "Huegel", "Felskopf",  # peaks, hills, rocks
        "Pass", "Strassenpass", "Wasserfall",                                            # passes, waterfalls
        "Haltestelle Bahn", "Uebrige Bahnen", "Aussichtspunkt",                          # stations, viewpoints
        "Gebaeude", "Offenes Gebaeude",                                                  # buildings, such as huts
    },
    "swissNAMES3D_PLY.csv": {
        "Grat", "Massiv", "Huegelzug",                                                   # ridges, massifs
        "See", "Seeteil", "Seeinsel", "Gletscher",                                       # lakes, glaciers
        "Tal", "Haupttal", "Gebiet", "Landschaftsname", "Grossregion",                   # valleys, regions
    },
}
SETTLEMENT_TYPES = ("Ort", "Ortsteil", "Quartier", "Quartierteil")
# How an answer calls each object type. The order is also the order in which the types of a
# place are named, so a settlement comes first and a peak before the cable car station on it.
OBJECT_TYPE_NAMES = {
    "Ort": "settlement", "Ortsteil": "part of a settlement", "Quartier": "district", "Quartierteil": "part of a district",
    "Alpiner Gipfel": "peak", "Hauptgipfel": "peak", "Gipfel": "peak", "Haupthuegel": "hill", "Huegel": "hill",
    "Massiv": "mountain massif", "Huegelzug": "range of hills", "Grat": "ridge", "Felskopf": "rock",
    "Pass": "pass", "Strassenpass": "pass",
    "See": "lake", "Seeteil": "part of a lake", "Seeinsel": "island", "Gletscher": "glacier", "Wasserfall": "waterfall",
    "Haltestelle Bahn": "railway station", "Uebrige Bahnen": "cable car station", "Aussichtspunkt": "viewpoint",
    "Gebaeude": "building", "Offenes Gebaeude": "building",
    "Tal": "valley", "Haupttal": "valley", "Gebiet": "area", "Landschaftsname": "region", "Grossregion": "region",
}
# The people of a settlement as swissNAMES3D publishes them, as a band from 1, the smallest;
# every other row has "k_W", no value, which is band 0
POPULATION_BANDS = {
    "< 20": 1, "20 bis 49": 2, "50 bis 99": 3, "100 bis 999": 4, "1'000 bis 1'999": 5,
    "2'000 bis 9'999": 6, "10'000 bis 49'999": 7, "50'000 bis 100'000": 8, "> 100'000": 9,
}
POPULATION_BAND_NAMES = {
    1: "fewer than 20 people", 2: "20 to 49 people", 3: "50 to 99 people", 4: "100 to 999 people",
    5: "1,000 to 1,999 people", 6: "2,000 to 9,999 people", 7: "10,000 to 49,999 people",
    8: "50,000 to 100,000 people", 9: "more than 100,000 people",
}
EXONYM = "Exonym"


def _join_with_and(words: List[str]) -> str:
    """Join words the way a sentence lists them, such as "peak, viewpoint and cable car station"."""
    if len(words) == 1:
        return words[0]
    return f"{', '.join(words[:-1])} and {words[-1]}"


class SwisstopoPlace(NamedTuple):
    """
    A named place of swissNAMES3D: one row as published, or several close rows of the same
    name merged into one, such as a lake and the district on its shore.
    """
    name: str
    object_types: Tuple[str, ...]  # such as ("Quartier", "See"), in the order of OBJECT_TYPE_NAMES
    population_band: int  # 0 when the place has no people, else a value of POPULATION_BANDS
    is_exonym: bool  # a name in another language, such as Genf for Genève
    altitude_m: float
    east_m: float   # LV95, the Swiss grid in metres
    north_m: float  # LV95

    @property
    def display_name(self) -> str:
        """The name shown to a reader, with the altitude, such as "Oerlikon (444 m)"."""
        return f"{self.name} ({self.altitude_m:.0f} m)"

    @property
    def description(self) -> str:
        """What the place is, such as "district and lake, fewer than 20 people, 887 m"."""
        type_names: List[str] = []
        for object_type in self.object_types:
            type_name = OBJECT_TYPE_NAMES[object_type]
            if type_name not in type_names:
                type_names.append(type_name)
        parts = [_join_with_and(type_names)]
        if self.population_band:
            parts.append(POPULATION_BAND_NAMES[self.population_band])
        parts.append(f"{self.altitude_m:.0f} m")
        return ", ".join(parts)


class SwisstopoPlaceSource:
    """Read the places of the newest swissNAMES3D edition, cached on disk as its archive."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "places"
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
        """Read the rows of the object types in OBJECT_TYPES_BY_FILE into memory once per process."""
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
                            object_types=(row["OBJEKTART"],),
                            population_band=POPULATION_BANDS.get(row["EINWOHNERKATEGORIE"], 0),
                            is_exonym=row["NAMEN_TYP"] == EXONYM,
                            altitude_m=float(row["Z"]),
                            east_m=float(row["E"]),
                            north_m=float(row["N"]),
                        )
                        places.append(place)
        # Published only once complete, so a parallel request never sees part of the places
        self._places = places
        logger.info(f"Loaded {len(places)} names of swissNAMES3D")
        return places
