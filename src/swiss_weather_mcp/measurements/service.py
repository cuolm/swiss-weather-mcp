"""Answer the measurement tools: what the MeteoSwiss and SLF stations measure near a place."""
import asyncio
from datetime import date
from typing import Optional

from ..places import PlaceFinder
from .current import CurrentAnswer, build_current_answer
from .extremes import ExtremesAnswer, build_extremes_answer
from .history import HistoryAnswer, HistoryValues, build_history_answer, check_day_count
from .meteoswiss_source import MeteoSwissMeasurementSource
from .slf_source import SLFMeasurementSource
from .snow import SnowAnswer, build_snow_answer


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

    async def read_current(self, location: str, nearby_location: Optional[str] = None) -> CurrentAnswer:
        """
        Read the latest measurements of the station chosen for the location.

        Add the slf_station block when an SLF station suits the location better than the MeteoSwiss station.
        Raise CannotAnswerError for an unknown or unclear location, and when none of the MAX_STATIONS_TRIED
        best-suited stations published a temperature in the last hour.
        """
        place = await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)
        return await asyncio.to_thread(build_current_answer, self.meteoswiss_source, self.slf_source, place)

    async def read_history(
        self,
        location: str,
        first_day: date,
        days: int,
        values: Optional[HistoryValues] = None,
        nearby_location: Optional[str] = None,
    ) -> HistoryAnswer:
        """
        Read the daily weather and snow near the location for 1 to MAX_HISTORY_DAYS UTC days from first_day.

        Raise CannotAnswerError when days is out of range, for an unknown or unclear location, for a first day
        today or later, and when no station has the values asked for.
        """
        check_day_count(days)
        place = await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)
        return await asyncio.to_thread(
            build_history_answer, self.meteoswiss_source, self.slf_source, place, first_day, days, values
        )

    async def read_snow(self, location: str, nearby_location: Optional[str] = None) -> SnowAnswer:
        """
        Read the current snow depth and the new snow for the location, each from its own best station.

        snow_depth or new_snow is None when no station close enough has a current value. Raise
        CannotAnswerError for an unknown or unclear location, and when neither has one.
        """
        place = await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)
        return await asyncio.to_thread(build_snow_answer, self.meteoswiss_source, self.slf_source, place)

    async def read_extremes(self, extreme: str) -> ExtremesAnswer:
        """
        Read where in Switzerland a measured value is at its extreme now, or for the snow this morning.

        Raise CannotAnswerError for an unknown extreme, and when MeteoSwiss, or SLF for the snow, publishes no
        measurements.
        """
        return await asyncio.to_thread(build_extremes_answer, self.meteoswiss_source, self.slf_source, extreme)
