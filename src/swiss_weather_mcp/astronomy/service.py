import asyncio
from datetime import date, datetime, timedelta
from typing import NotRequired, Optional, TypedDict

from ..formatting import format_swiss_time
from ..places import PlaceFinder
from .moon import calculate_moon_day
from .sun import calculate_sun_day

HALF_A_MINUTE = timedelta(seconds=30)

# The Unicode moon symbols, named like the phases, U+1F311 to U+1F318
MOON_PHASE_TO_EMOJI = {
    "New Moon": "🌑",
    "Waxing Crescent": "🌒",
    "First Quarter": "🌓",
    "Waxing Gibbous": "🌔",
    "Full Moon": "🌕",
    "Waning Gibbous": "🌖",
    "Last Quarter": "🌗",
    "Waning Crescent": "🌘",
}


class SunAnswer(TypedDict):
    """The astronomy_sun answer; a moment is None when it does not happen that day."""
    place: str
    date: str
    civil_twilight_begin: Optional[str]
    sunrise: Optional[str]
    sunset: Optional[str]
    civil_twilight_end: Optional[str]


class MoonPhaseTimeField(TypedDict):
    """The moon_phase_time field of a day on which New Moon, First Quarter, Full Moon or Last Quarter happens."""
    moon_phase_time: NotRequired[Optional[str]]


class MoonAnswer(MoonPhaseTimeField):
    """The astronomy_moon answer; moonrise or moonset is None when it does not happen that day."""
    place: str
    date: str
    moonrise: Optional[str]
    moonset: Optional[str]
    moon_phase: str
    moon_phase_emoji: str
    illuminated_percent: int


def _format_to_the_minute(moment: Optional[datetime]) -> Optional[str]:
    """Format a moment as Swiss local time rounded to the nearest minute, or return None for None."""
    if moment is None:
        return None
    # format_swiss_time drops the seconds, so adding half a minute first rounds instead
    return format_swiss_time(moment + HALF_A_MINUTE)


class AstronomyService:
    """Answer when the Moon and the Sun rise and set at a place, the Moon's phase and the civil twilight."""

    def __init__(self, place_finder: PlaceFinder):
        self.place_finder = place_finder

    async def read_sun(self, location: str, day: date, nearby_location: Optional[str] = None) -> SunAnswer:
        """
        Read when civil twilight begins, the Sun rises and sets, and civil twilight ends on a Swiss calendar day.

        Raise CannotAnswerError for an unknown location.
        """
        place = await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)
        # In a worker thread, so the calculation does not hold up other requests
        sun_day = await asyncio.to_thread(calculate_sun_day, day, place.latitude_deg, place.longitude_deg)
        return {
            "place": place.name,
            "date": day.isoformat(),
            "civil_twilight_begin": _format_to_the_minute(sun_day.civil_twilight_begin),
            "sunrise": _format_to_the_minute(sun_day.sunrise),
            "sunset": _format_to_the_minute(sun_day.sunset),
            "civil_twilight_end": _format_to_the_minute(sun_day.civil_twilight_end),
        }

    async def read_moon(self, location: str, day: date, nearby_location: Optional[str] = None) -> MoonAnswer:
        """
        Read the moonrise, moonset and phase on a Swiss calendar day, and how much of the Moon is lit at 12:00.

        Raise CannotAnswerError for an unknown location.
        """
        place = await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)
        # In a worker thread, so the calculation does not hold up other requests
        moon_day = await asyncio.to_thread(calculate_moon_day, day, place.latitude_deg, place.longitude_deg)

        phase_time_field: MoonPhaseTimeField = {}
        if moon_day.phase_time is not None:
            phase_time_field = {"moon_phase_time": _format_to_the_minute(moon_day.phase_time)}
        return {
            "place": place.name,
            "date": day.isoformat(),
            "moonrise": _format_to_the_minute(moon_day.moonrise),
            "moonset": _format_to_the_minute(moon_day.moonset),
            "moon_phase": moon_day.phase,
            "moon_phase_emoji": MOON_PHASE_TO_EMOJI[moon_day.phase],
            **phase_time_field,
            "illuminated_percent": round(moon_day.illuminated_percent),
        }
