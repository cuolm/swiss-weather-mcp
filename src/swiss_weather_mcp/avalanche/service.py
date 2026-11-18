"""Answer the avalanche_bulletin tool: the SLF avalanche bulletin valid now for a place."""
import asyncio
import logging
from typing import NotRequired, Optional, TypedDict

from ..errors import CannotAnswerError
from ..formatting import describe_source, format_swiss_time
from ..places import PlaceFinder, SwisstopoPlace
from .source import AvalancheBulletin, AvalancheProblem, DangerRating, Elevation, SLFBulletinSource


logger = logging.getLogger(__name__)

# The danger levels, https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/about-the-avalanche-bulletin/danger-levels/
LEVEL_TO_NUMBER = {"low": 1, "moderate": 2, "considerable": 3, "high": 4, "very_high": 5}
# Written after the number, as SLF does in "considerable (3-)"
SUBDIVISION_TO_SIGN = {"minus": "-", "neutral": "=", "plus": "+"}

SOURCE = describe_source(uses_meteoswiss=False, uses_slf=True, uses_swisstopo=True)
# SLF's map of the bulletin valid now, a GIF of Switzerland with the danger levels, from its bulletin document API
MAP_URL = "https://aws.slf.ch/api/bulletin/document/iconmap/en/gif"


class ProblemBlock(TypedDict):
    """The block of one avalanche problem."""
    problem: str
    danger_level: str
    time_of_day: str
    heights: Optional[str]
    aspects: list[str]
    summary: Optional[str]
    description: str


class DangerLevelFields(TypedDict):
    """The danger level of the whole day, and in spring the one of the wet snow later in the day."""
    danger_level: NotRequired[str]
    danger_level_later: NotRequired[str]


class BulletinAnswer(DangerLevelFields):
    """The avalanche_bulletin answer."""
    place: str
    place_altitude_m: int
    regions: list[str]
    published_at: str
    valid_from: str
    valid_until: str
    next_update: Optional[str]
    avalanche_problems: list[ProblemBlock]
    snowpack: str
    outlook: str
    weather_forecast: Optional[str]
    weather_review: Optional[str]
    map: str
    source: str


def _format_danger_level(danger_rating: DangerRating) -> str:
    """Write a danger level as SLF does, such as "considerable (3-)", or "low (1)" without a subdivision."""
    level_name = danger_rating.level.replace("_", " ")
    level_number = LEVEL_TO_NUMBER[danger_rating.level]
    if danger_rating.subdivision is None:
        return f"{level_name} ({level_number})"
    sign = SUBDIVISION_TO_SIGN[danger_rating.subdivision]
    return f"{level_name} ({level_number}{sign})"


def _build_danger_level_fields(danger_ratings: list[DangerRating]) -> DangerLevelFields:
    """Build the danger level of the whole day, and the one later in the day when SLF gives it."""
    danger_level_fields: DangerLevelFields = {}
    for danger_rating in danger_ratings:
        danger_level = _format_danger_level(danger_rating)
        if danger_rating.time_period == "all_day":
            danger_level_fields["danger_level"] = danger_level
        elif danger_rating.time_period == "later":
            danger_level_fields["danger_level_later"] = danger_level
        else:
            logger.warning(f"Left out a danger level of the unknown time period {danger_rating.time_period!r}")
    return danger_level_fields


def _describe_heights(elevation: Optional[Elevation]) -> Optional[str]:
    """Say at which heights an avalanche problem applies, such as "above 1800 m", or None when SLF gives none."""
    if elevation is None:
        return None
    lower_bound_m = elevation.lower_bound_m
    upper_bound_m = elevation.upper_bound_m
    if lower_bound_m is not None and upper_bound_m is not None:
        return f"between {lower_bound_m} and {upper_bound_m} m"
    if lower_bound_m is not None:
        return f"above {lower_bound_m} m"
    if upper_bound_m is not None:
        return f"below {upper_bound_m} m"
    return "all heights"


def _build_problem_block(problem: AvalancheProblem) -> ProblemBlock:
    """Build the block of one avalanche problem."""
    problem_name = problem.problem_type.replace("_", " ")
    danger_level = _format_danger_level(problem.danger_rating)
    time_of_day = problem.danger_rating.time_period.replace("_", " ")
    heights = _describe_heights(problem.elevation)
    return {
        "problem": problem_name,
        "danger_level": danger_level,
        "time_of_day": time_of_day,
        "heights": heights,
        "aspects": problem.aspects,
        "summary": problem.summary,
        "description": problem.description,
    }


def _build_answer(place: SwisstopoPlace, bulletin: AvalancheBulletin) -> BulletinAnswer:
    """Build the answer from the bulletin that covers a place."""
    next_update = None
    if bulletin.next_update is not None:
        next_update = format_swiss_time(bulletin.next_update)
    danger_level_fields = _build_danger_level_fields(bulletin.danger_ratings)
    problem_blocks = []
    for problem in bulletin.problems:
        problem_block = _build_problem_block(problem)
        problem_blocks.append(problem_block)
    return {
        "place": place.name,
        "place_altitude_m": round(place.altitude_m),
        "regions": bulletin.region_names,
        "published_at": format_swiss_time(bulletin.published_at),
        "valid_from": format_swiss_time(bulletin.valid_from),
        "valid_until": format_swiss_time(bulletin.valid_until),
        "next_update": next_update,
        **danger_level_fields,
        "avalanche_problems": problem_blocks,
        "snowpack": bulletin.snowpack,
        "outlook": bulletin.outlook,
        "weather_forecast": bulletin.weather_forecast,
        "weather_review": bulletin.weather_review,
        "map": MAP_URL,
        "source": SOURCE,
    }


def build_bulletin_answer(bulletin_source: SLFBulletinSource, place: SwisstopoPlace) -> BulletinAnswer:
    """
    Build the avalanche_bulletin answer for a place; run it in a worker thread, as it downloads.

    Raise CannotAnswerError when SLF has no bulletin valid now, and when none of them covers the place.
    """
    bulletins = bulletin_source.read_bulletins()
    if not bulletins:
        raise CannotAnswerError(
            "SLF has no avalanche bulletin valid now. In winter SLF publishes one every day at 17:00; in summer and "
            "autumn only after heavy snowfall."
        )
    for bulletin in bulletins:
        if bulletin.covers(place):
            return _build_answer(place, bulletin)
    raise CannotAnswerError(
        f"{place.display_name} lies in none of the regions of the SLF avalanche bulletin valid now. The bulletin "
        f"covers the Alps and the Jura, never the Plateau, and some regions only on some days."
    )


class AvalancheService:
    """Answer the avalanche bulletin tool: the SLF bulletin valid now for a place."""

    def __init__(self, place_finder: PlaceFinder, bulletin_source: SLFBulletinSource):
        self.place_finder = place_finder
        self.bulletin_source = bulletin_source

    async def read_bulletin(self, location: str, nearby_location: Optional[str] = None) -> BulletinAnswer:
        """
        Read the SLF avalanche bulletin valid now for the location.

        Raise CannotAnswerError for an unknown or unclear location, when SLF has no bulletin valid now, and
        when none of them covers the location.
        """
        place = await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)
        return await asyncio.to_thread(build_bulletin_answer, self.bulletin_source, place)
