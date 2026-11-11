"""Answer the avalanche_bulletin tool: the SLF avalanche bulletin valid now for a place."""
import asyncio
from typing import Any, Dict, Optional

from ..errors import CannotAnswerError
from ..formatting import format_swiss_time
from ..places import PlaceFinder, SwisstopoPlace
from .source import AvalancheBulletin, AvalancheProblem, DangerRating, Elevation, SLFBulletinSource

# The danger levels, https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/about-the-avalanche-bulletin/danger-levels/
LEVEL_TO_NUMBER = {"low": 1, "moderate": 2, "considerable": 3, "high": 4, "very_high": 5}
# Written after the number, as SLF does in "considerable (3-)"
SUBDIVISION_TO_SIGN = {"minus": "-", "neutral": "=", "plus": "+"}

# SLF asks for a visible source with a link, https://www.slf.ch/en/services-and-products/slf-data-service/
SOURCE = "Avalanche bulletin of SLF, the WSL Institute for Snow and Avalanche Research, https://www.slf.ch"
# SLF's map of the bulletin valid now, a GIF of Switzerland with the danger levels, from its bulletin document API
MAP_URL = "https://aws.slf.ch/api/bulletin/document/iconmap/en/gif"


def _format_danger_level(danger_rating: DangerRating) -> str:
    """Write a danger level as SLF does, such as "considerable (3-)", or "low (1)" without a subdivision."""
    level_name = danger_rating.level.replace("_", " ")
    level_number = LEVEL_TO_NUMBER[danger_rating.level]
    if danger_rating.subdivision is None:
        return f"{level_name} ({level_number})"
    sign = SUBDIVISION_TO_SIGN[danger_rating.subdivision]
    return f"{level_name} ({level_number}{sign})"


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


def _build_problem_block(problem: AvalancheProblem) -> Dict[str, Any]:
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


def _build_answer(place: SwisstopoPlace, bulletin: AvalancheBulletin) -> Dict[str, Any]:
    """Build the answer from the bulletin that covers a place."""
    next_update = None
    if bulletin.next_update is not None:
        next_update = format_swiss_time(bulletin.next_update)
    answer: Dict[str, Any] = {
        "place": place.name,
        "place_altitude_m": round(place.altitude_m),
        "regions": bulletin.region_names,
        "published_at": format_swiss_time(bulletin.published_at),
        "valid_from": format_swiss_time(bulletin.valid_from),
        "valid_until": format_swiss_time(bulletin.valid_until),
        "next_update": next_update,
    }

    # The danger level of the whole day, and in spring the one of the wet snow later in the day
    for danger_rating in bulletin.danger_ratings:
        if danger_rating.time_period == "all_day":
            danger_level_key = "danger_level"
        else:
            danger_level_key = f"danger_level_{danger_rating.time_period}"
        answer[danger_level_key] = _format_danger_level(danger_rating)
    problem_blocks = []
    for problem in bulletin.problems:
        problem_block = _build_problem_block(problem)
        problem_blocks.append(problem_block)
    answer["avalanche_problems"] = problem_blocks

    answer["snowpack"] = bulletin.snowpack
    answer["outlook"] = bulletin.outlook
    answer["weather_forecast"] = bulletin.weather_forecast
    answer["weather_review"] = bulletin.weather_review
    answer["map"] = MAP_URL
    answer["source"] = SOURCE
    return answer


def build_bulletin_answer(bulletin_source: SLFBulletinSource, place: SwisstopoPlace) -> Dict[str, Any]:
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

    async def _find_place(self, location: str, nearby_location: Optional[str]) -> SwisstopoPlace:
        """Find the place a user means in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)

    async def read_bulletin(self, location: str, nearby_location: Optional[str] = None) -> Dict[str, Any]:
        """
        Read the SLF avalanche bulletin valid now for the location.

        Raise CannotAnswerError for an unknown location, when SLF has no bulletin valid now, and when none of
        them covers the location.
        """
        place = await self._find_place(location, nearby_location)
        return await asyncio.to_thread(build_bulletin_answer, self.bulletin_source, place)
