import asyncio
from datetime import date
from typing import Any, Dict, Optional

from ..places import PlaceFinder, SwisstopoPlace
from .current import build_current_answer
from .extremes import build_extremes_answer
from .history import HistoryValues, build_history_answer, check_day_count
from .meteoswiss_source import MeteoSwissMeasurementSource
from .slf_source import SLFMeasurementSource
from .snow import build_snow_answer


class MeasurementService:
    """Answer the measurement tools: the weather now, past days and snow at a place, and extremes in Switzerland."""

    def __init__(
        self,
        place_finder: PlaceFinder,
        meteoswiss_source: MeteoSwissMeasurementSource,
        slf_source: SLFMeasurementSource,
    ):
        self.place_finder = place_finder
        self.meteoswiss_source = meteoswiss_source
        self.slf_source = slf_source

    async def _find_place(self, location: str, nearby_location: Optional[str]) -> SwisstopoPlace:
        """Find the place a user means in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)

    async def read_current(self, location: str, nearby_location: Optional[str] = None) -> Dict[str, Any]:
        """
        Read the latest measurements of the station chosen for the location.

        Add the slf_station block when an SLF station suits the location better than the MeteoSwiss station.
        Raise CannotAnswerError for an unknown location or when no station publishes a temperature.
        """
        place = await self._find_place(location, nearby_location)
        return await asyncio.to_thread(build_current_answer, self.meteoswiss_source, self.slf_source, place)

    async def read_history(
        self,
        location: str,
        first_day: date,
        days: int,
        values: Optional[HistoryValues] = None,
        nearby_location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Read the daily weather and snow of the stations chosen for the location, for 1 to MAX_HISTORY_DAYS UTC days from first_day.

        Raise CannotAnswerError when days is out of range, for an unknown location, for today or later, and
        when no station has the values asked for.
        """
        check_day_count(days)
        place = await self._find_place(location, nearby_location)
        return await asyncio.to_thread(build_history_answer, self.meteoswiss_source, place, first_day, days, values)

    async def read_snow(self, location: str, nearby_location: Optional[str] = None) -> Dict[str, Any]:
        """
        Read the current snow depth and the new snow for the location, each from its own best station.

        snow_depth or new_snow is None when no station close enough has a current value. Raise
        CannotAnswerError for an unknown location and when neither has one.
        """
        place = await self._find_place(location, nearby_location)
        return await asyncio.to_thread(build_snow_answer, self.meteoswiss_source, self.slf_source, place)

    async def read_extremes(self, extreme: str) -> Dict[str, Any]:
        """
        Read where in Switzerland a measured value is at its extreme now.

        extreme is one of EXTREME_NAME_TO_EXTREME. Raise CannotAnswerError for an unknown extreme or when
        MeteoSwiss publishes no measurements.
        """
        return await asyncio.to_thread(build_extremes_answer, self.meteoswiss_source, extreme)
