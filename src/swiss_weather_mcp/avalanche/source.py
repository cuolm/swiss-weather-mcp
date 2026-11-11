"""
Read the avalanche bulletin valid now from the SLF bulletin API, https://aws.slf.ch/api/bulletin/caaml

What SLF publishes:
- In winter a bulletin every day at 17:00, valid until 17:00 the next day, and an update at
  08:00 when the danger is considerable (level 3) or higher anywhere, or when needed. In summer
  and autumn only after heavy snowfall. Rarely an unscheduled one in between.
- Switzerland is split into small warning regions, such as CH-3112 Southern Glarus-Grosstal.
  The forecasters group the regions with the same danger into areas, and each area is one
  bulletin: its outline, its regions, the danger level, the avalanche problems and the texts.
  The outline is the merged shape of all its regions, and the outlines of one day never
  overlap. In winter 2025/26 there were 2 to 15 bulletins a day, about 80 KB in all. They
  cover the Alps and the Jura, never the Plateau.
- The danger levels are low (1), moderate (2), considerable (3), high (4) and very high (5).
  From level 2, a dry-snow danger has a subdivision: towards the bottom end (-), in the middle
  (=) or towards the top end (+), https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/about-the-avalanche-bulletin/danger-levels/
- A bulletin has one danger level for the whole day and, mostly in spring, a second one for
  the wet snow later in the day. Each avalanche problem, such as wind slab, has its own danger
  level, heights, aspects (the directions the slopes face) and texts. SLF leaves out what a
  problem does not have: a wet-snow problem often has no heights, aspects or summary.
- Four texts are the same in all bulletins of a day: the snowpack, the outlook, and the weather
  forecast and review, which some bulletins do not have. The texts are HTML with only h1, h2,
  p, ul, li and br.
- The API has two versions; this server reads V4, where heights are strings, the whole mountain
  is an empty elevation, and empty values are left out. When no bulletin is valid, the feature
  list is empty.

    bulletin/caaml/v4/en/geojson  (the bulletins valid now, as GeoJSON, one feature each)
    ┌────────────────────────────────────────────────────────────────────────────┐
    │ {"type": "FeatureCollection", "features": [                                │
    │   {"geometry": {"type": "Polygon", "coordinates": [[[9.13, 46.98], ...]]}, │  outline, [longitude, latitude]
    │    "properties": {                                                         │
    │      "validTime": {"startTime": "2026-02-14T07:00:00Z", ...},              │  times in UTC
    │      "regions": [{"regionID": "CH-3112", "name": "Southern Glarus-..."}],  │
    │      "dangerRatings": [{"mainValue": "considerable", ...}],                │  levels and subdivisions
    │      "avalancheProblems": [{"problemType": "wind_slab", ...}],             │
    │      "snowpackStructure": {"comment": "<h1>Snowpack</h1>..."}, ...}},      │  texts in HTML
    │   ...]}                                                                    │
    └────────────────────────────────────────────────────────────────────────────┘

How we use it, while the service finds the bulletin of a place:
1. Read the bulletins on each question; the file is downloaded again when it is older than
   10 minutes, so a new bulletin is used soon after SLF publishes it.
2. Keep the values the answer gives, and turn the HTML texts into plain text, a line for each
   heading, paragraph and list item.
3. Turn each point of the outlines into LV95 with swisstopo's approximate formulas. A bulletin
   covers a place when the place lies inside one part of its outline and not in one of that
   part's holes.

    <cache dir>/slf/
    └── avalanche_bulletin.json   (the bulletins valid now, downloaded again after 10 minutes)
"""
import logging
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from ..coordinates import calculate_lv95_east_m, calculate_lv95_north_m
from ..downloads import read_recent_json
from ..places import SwisstopoPlace
from ..polygons import Polygon, Ring, polygons_contain

logger = logging.getLogger(__name__)

BULLETIN_URL = "https://aws.slf.ch/api/bulletin/caaml/v4/en/geojson"
# The bulletin changes at 17:00, at 08:00 and rarely in between, at times a few minutes late, and SLF asks
# that the content is "always correct and up to date"
BULLETIN_MAX_AGE = timedelta(minutes=10)

# The tags after which the text goes on in a new line
LINE_TAGS = {"h1", "h2", "p", "ul", "li", "br"}
LIST_ITEM_MARK = "- "

# GeoJSON writes a point as [longitude, latitude]
LONGITUDE = 0
LATITUDE = 1


class _TextCollector(HTMLParser):
    """Collect the plain text of an SLF bulletin text, a line for each heading, paragraph and list item."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: List[str] = []
        self._line_parts: List[str] = []

    def _end_line(self) -> None:
        """Add the text collected since the last line as a line of its own, with its spaces tidied."""
        line_text = "".join(self._line_parts)
        words = line_text.split()
        if words:
            line = " ".join(words)
            self.lines.append(line)
        self._line_parts = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in LINE_TAGS:
            self._end_line()
        if tag == "li":
            self._line_parts.append(LIST_ITEM_MARK)

    def handle_endtag(self, tag: str) -> None:
        if tag in LINE_TAGS:
            self._end_line()

    def handle_data(self, data: str) -> None:
        self._line_parts.append(data)

    def close(self) -> None:
        super().close()
        self._end_line()


def _convert_html_to_text(html_text: str) -> str:
    """Turn the HTML of an SLF bulletin text into plain text, a line for each heading, paragraph and list item."""
    collector = _TextCollector()
    collector.feed(html_text)
    collector.close()
    return "\n".join(collector.lines)


class DangerRating(NamedTuple):
    """A danger level of a bulletin or of one of its avalanche problems."""
    level: str  # "low", "moderate", "considerable", "high" or "very_high"
    subdivision: Optional[str]  # "minus", "neutral" or "plus"; None for level 1 and for wet snow
    time_period: str  # "all_day", or "later" for the wet snow as the day warms up


class Elevation(NamedTuple):
    """The heights an avalanche problem applies at; both bounds None for the whole mountain."""
    lower_bound_m: Optional[int]
    upper_bound_m: Optional[int]


class AvalancheProblem(NamedTuple):
    """One avalanche problem of a bulletin, such as wind slab."""
    problem_type: str  # such as "wind_slab", "persistent_weak_layers" or "wet_snow"
    danger_rating: DangerRating
    elevation: Optional[Elevation]  # None when SLF gives no heights
    aspects: List[str]  # the directions the slopes face, such as ["N", "NE"]; empty when SLF gives none
    summary: Optional[str]  # SLF's sentence with the danger level, aspects and heights
    description: str


class AvalancheBulletin(NamedTuple):
    """One bulletin: an area of regions with the same danger, valid now."""
    published_at: datetime
    valid_from: datetime
    valid_until: datetime
    next_update: Optional[datetime]
    region_names: List[str]
    danger_ratings: List[DangerRating]  # the one for the whole day first
    problems: List[AvalancheProblem]
    snowpack: str
    outlook: str
    weather_forecast: Optional[str]
    weather_review: Optional[str]
    outline: List[Polygon]  # the parts of the outline on LV95

    def covers(self, place: SwisstopoPlace) -> bool:
        """Tell whether a place lies inside the outline of the bulletin."""
        return polygons_contain(self.outline, place.east_m, place.north_m)


def _parse_swiss_data(json_object: Dict[str, Any]) -> Dict[str, Any]:
    """Return the values SLF adds to the CAAML standard for Switzerland, or an empty dict when there are none."""
    custom_data = json_object.get("customData", {})
    return custom_data.get("CH", {})


def _parse_danger_rating(rating_object: Dict[str, Any], level_key: str) -> DangerRating:
    """Read a danger level, whose value a bulletin keeps under "mainValue" and a problem under "dangerRatingValue"."""
    swiss_data = _parse_swiss_data(rating_object)
    subdivision = swiss_data.get("subdivision")
    return DangerRating(
        level=rating_object[level_key],
        subdivision=subdivision,
        time_period=rating_object["validTimePeriod"],
    )


def _parse_elevation(problem_object: Dict[str, Any]) -> Optional[Elevation]:
    """Read the heights of an avalanche problem, or None when SLF gives none."""
    if "elevation" not in problem_object:
        return None
    elevation_object = problem_object["elevation"]
    lower_bound_m = None
    upper_bound_m = None
    # Such as "2200", in metres
    if "lowerBound" in elevation_object:
        lower_bound_m = int(elevation_object["lowerBound"])
    if "upperBound" in elevation_object:
        upper_bound_m = int(elevation_object["upperBound"])
    return Elevation(lower_bound_m, upper_bound_m)


def _parse_problem(problem_object: Dict[str, Any]) -> AvalancheProblem:
    """Read one avalanche problem of a bulletin."""
    danger_rating = _parse_danger_rating(problem_object, "dangerRatingValue")
    elevation = _parse_elevation(problem_object)
    aspects = problem_object.get("aspects", [])
    swiss_data = _parse_swiss_data(problem_object)
    summary = swiss_data.get("coreZoneText")
    description = _convert_html_to_text(problem_object["comment"])
    return AvalancheProblem(
        problem_type=problem_object["problemType"],
        danger_rating=danger_rating,
        elevation=elevation,
        aspects=aspects,
        summary=summary,
        description=description,
    )


def _parse_optional_text(properties: Dict[str, Any], text_name: str) -> Optional[str]:
    """Read one of the texts some bulletins do not have, as plain text, or None when it is missing."""
    if text_name not in properties:
        return None
    return _convert_html_to_text(properties[text_name]["comment"])


def _parse_ring(ring_points: List[List[float]]) -> Ring:
    """Turn a ring of GeoJSON [longitude, latitude] points into (east, north) points on LV95."""
    ring = []
    for point in ring_points:
        latitude_deg = point[LATITUDE]
        longitude_deg = point[LONGITUDE]
        east_m = calculate_lv95_east_m(latitude_deg, longitude_deg)
        north_m = calculate_lv95_north_m(latitude_deg, longitude_deg)
        ring.append((east_m, north_m))
    return ring


def _parse_outline(geometry: Dict[str, Any]) -> List[Polygon]:
    """Read the parts of a bulletin's outline on LV95, from a Polygon of one part or a MultiPolygon of several."""
    if geometry["type"] == "Polygon":
        geojson_polygons = [geometry["coordinates"]]
    else:
        geojson_polygons = geometry["coordinates"]
    polygons = []
    for geojson_polygon in geojson_polygons:
        polygon = []
        for ring_points in geojson_polygon:
            ring = _parse_ring(ring_points)
            polygon.append(ring)
        polygons.append(polygon)
    return polygons


def _parse_bulletin(feature: Dict[str, Any]) -> AvalancheBulletin:
    """Read one feature of the GeoJSON as a bulletin."""
    properties = feature["properties"]
    # Such as "2026-02-14T07:00:00Z", which fromisoformat reads as UTC
    published_at = datetime.fromisoformat(properties["publicationTime"])
    valid_from = datetime.fromisoformat(properties["validTime"]["startTime"])
    valid_until = datetime.fromisoformat(properties["validTime"]["endTime"])
    next_update = None
    if "nextUpdate" in properties:
        next_update = datetime.fromisoformat(properties["nextUpdate"])

    region_names = []
    for region in properties["regions"]:
        region_names.append(region["name"])
    danger_ratings = []
    for rating_object in properties["dangerRatings"]:
        danger_rating = _parse_danger_rating(rating_object, "mainValue")
        danger_ratings.append(danger_rating)
    problems = []
    for problem_object in properties["avalancheProblems"]:
        problem = _parse_problem(problem_object)
        problems.append(problem)

    snowpack = _convert_html_to_text(properties["snowpackStructure"]["comment"])
    outlook_parts = []
    for tendency in properties["tendency"]:
        outlook_part = _convert_html_to_text(tendency["comment"])
        outlook_parts.append(outlook_part)
    outlook = "\n".join(outlook_parts)
    weather_forecast = _parse_optional_text(properties, "weatherForecast")
    weather_review = _parse_optional_text(properties, "weatherReview")
    outline = _parse_outline(feature["geometry"])

    return AvalancheBulletin(
        published_at=published_at,
        valid_from=valid_from,
        valid_until=valid_until,
        next_update=next_update,
        region_names=region_names,
        danger_ratings=danger_ratings,
        problems=problems,
        snowpack=snowpack,
        outlook=outlook,
        weather_forecast=weather_forecast,
        weather_review=weather_review,
        outline=outline,
    )


class SLFBulletinSource:
    """Read the SLF avalanche bulletins valid now."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir / "slf"

    def read_bulletins(self) -> List[AvalancheBulletin]:
        """Read the bulletins valid now, one for each area of regions with the same danger; none when SLF has none."""
        bulletin_file = self.cache_dir / "avalanche_bulletin.json"
        feature_collection = read_recent_json(BULLETIN_URL, bulletin_file, BULLETIN_MAX_AGE)
        bulletins = []
        for feature in feature_collection["features"]:
            bulletin = _parse_bulletin(feature)
            bulletins.append(bulletin)
        return bulletins
