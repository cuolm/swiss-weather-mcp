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
   group. A name of one group is found. A name of several groups is refused with one
   option per group, written so the lookup accepts it, with the nearest MeteoSwiss village:

    "Blausee", 6 groups
      "Blausee (lake, 2205 m, near Bettmeralp)"
      "Blausee (district, 887 m, near Blausee-Mitholz)"
      ...

   Free text that starts with such a name, such as "Blausee near Bettmeralp", picks the
   option whose village it names.

    <cache dir>/locations/
    └── swissnames3d_2026_2056.csv.zip   (the newest edition, replaced when a newer one appears)
"""
import csv
import io
import logging
import re
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

import requests

from ..errors import CannotAnswerError
from ..opendata import REQUEST_TIMEOUT_SECONDS, download_file
from .names import Position, calculate_distance_m, normalise_location

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
    """Find a swisstopo place by its name, by an option for a name of several places, or by free text."""

    def __init__(self, place_source: SwisstopoPlaceSource, find_nearest_village_name: Callable[[Position], str]):
        self.place_source = place_source
        # Names the village an option is "near", such as Bettmeralp
        self.find_nearest_village_name = find_nearest_village_name
        self._place_groups_by_name: Dict[str, List[List[SwisstopoPlace]]] = {}

    def _ensure_place_groups(self) -> None:
        """Read the places and group them by name, once per process."""
        if self._place_groups_by_name:
            return

        places = self.place_source.load_places()
        places_by_name: Dict[str, List[SwisstopoPlace]] = defaultdict(list)
        for place in places:
            places_by_name[normalise_location(place.name)].append(place)
        place_groups_by_name = {}
        for name, named_places in places_by_name.items():
            place_groups_by_name[name] = _group_close_places(named_places)
        # Published only once complete, so a parallel request never sees part of the groups
        self._place_groups_by_name = place_groups_by_name

    def _build_option(self, place: SwisstopoPlace) -> str:
        """Build the text that picks one of several places, such as "Blausee (lake, 2205 m, near Bettmeralp)"."""
        village_name = self.find_nearest_village_name(place)
        return f"{place.name} ({place.object_type_name}, {place.altitude_m:.0f} m, near {village_name})"

    def _build_options_error(self, groups: List[List[SwisstopoPlace]]) -> CannotAnswerError:
        """Build the refusal of a name of several places, with one option per place."""
        name = groups[0][0].name
        option_lines = []
        for group in groups:
            option = self._build_option(_choose_place(group))
            option_lines.append(f'  "{option}"')
        listed_options = "\n".join(option_lines)
        return CannotAnswerError(
            f"'{name}' is the name of {len(groups)} places. Ask again with one of these, written exactly as "
            f"shown:\n{listed_options}"
        )

    def _split_known_name(self, location: str) -> Tuple[Optional[str], str]:
        """Split free text into the longest place name it starts with and the rest, both normalised."""
        words = location.split()
        for word_count in range(len(words) - 1, 0, -1):
            name = normalise_location(" ".join(words[:word_count]))
            if name in self._place_groups_by_name:
                rest = normalise_location(" ".join(words[word_count:]))
                return name, rest
        return None, ""

    def _pick_place(self, groups: List[List[SwisstopoPlace]], location: str, rest: str) -> SwisstopoPlace:
        """Pick one of several places by an option or by the village the rest of the text names."""
        normalised_location = normalise_location(location)
        for group in groups:
            place = _choose_place(group)
            if normalised_location == normalise_location(self._build_option(place)):
                return place
        for group in groups:
            place = _choose_place(group)
            village_name = normalise_location(self.find_nearest_village_name(place))
            # Whole words only, so a village such as Au is not found inside another word
            if re.search(rf"\b{re.escape(village_name)}\b", rest):
                return place
        raise self._build_options_error(groups)

    def find_place(self, location: str) -> Optional[SwisstopoPlace]:
        """
        Find the place for a name, an option such as "Blausee (lake, 2205 m, near Bettmeralp)",
        or free text such as "Blausee near Bettmeralp". Return None for an unknown name.

        Raise CannotAnswerError for a name of several places with no option or village to pick
        one, listing the options to ask again with.
        """
        self._ensure_place_groups()
        normalised_location = normalise_location(location)
        if normalised_location in self._place_groups_by_name:
            groups = self._place_groups_by_name[normalised_location]
            rest = ""
        else:
            name, rest = self._split_known_name(location)
            if name is None:
                return None
            groups = self._place_groups_by_name[name]

        if len(groups) == 1:
            return _choose_place(groups[0])
        return self._pick_place(groups, location, rest)
