"""
Find the MeteoSwiss point a location is answered for.

How we use it:
1. Look the location up in the MeteoSwiss points, see meteoswiss_points.py.
2. Only when it is not a MeteoSwiss point, look it up in the swisstopo places, see
   swisstopo_places.py, and answer for the MeteoSwiss point nearest to the place.
3. Refuse a location that neither of them knows.
"""
import logging
import threading
from pathlib import Path
from typing import NamedTuple, Optional, Union

import requests

from ..errors import CannotAnswerError
from .meteoswiss_points import MeteoSwissPoint, MeteoSwissPointLookup, MeteoSwissPointSource
from .swisstopo_places import SwisstopoPlace, SwisstopoPlaceLookup, SwisstopoPlaceSource

logger = logging.getLogger(__name__)


class ResolvedLocation(NamedTuple):
    """The MeteoSwiss point a location is answered for, and the swisstopo place when the location is not a point."""
    point: MeteoSwissPoint
    place: Optional[SwisstopoPlace]

    @property
    def asked_place(self) -> Union[SwisstopoPlace, MeteoSwissPoint]:
        """The place the user asked about: the swisstopo place, or else the MeteoSwiss point."""
        if self.place is not None:
            return self.place
        return self.point


class LocationFinder:
    """
    Find the MeteoSwiss point for a location. If the location is not a MeteoSwiss point, search
    the swisstopo places.
    """

    def __init__(self, cache_dir: Path):
        meteoswiss_point_source = MeteoSwissPointSource(cache_dir)
        swisstopo_place_source = SwisstopoPlaceSource(cache_dir)
        self.meteoswiss_point_lookup = MeteoSwissPointLookup(meteoswiss_point_source)
        self.swisstopo_place_lookup = SwisstopoPlaceLookup(
            swisstopo_place_source, self.meteoswiss_point_lookup.find_nearest_village_name
        )

    def _load_swisstopo_places(self) -> None:
        """Load the swisstopo places, or log why not, so the first question that needs them tries again."""
        try:
            self.swisstopo_place_lookup.load_place_groups()
        except requests.RequestException as error:
            logger.warning(
                f"Could not load the swisstopo places at start, the first question that needs them tries again: {error}"
            )

    def start_loading_swisstopo_places(self) -> threading.Thread:
        """
        Load the swisstopo places in a background thread, so the first question about a place
        MeteoSwiss does not list does not wait for their download. Return the thread.
        """
        thread = threading.Thread(target=self._load_swisstopo_places, name="load-swisstopo-places", daemon=True)
        thread.start()
        return thread

    def find_location(self, location: str) -> ResolvedLocation:
        """
        Find the MeteoSwiss point for a place name or a Swiss postal code.

        Case, accents and periods are ignored, and names in other languages such as "Genf" work
        too. A name that is not a MeteoSwiss point is looked up in the swisstopo places and
        answered for by the nearest MeteoSwiss point. Raise CannotAnswerError for an unknown
        location, and for a name of several places, listing them as options to ask again with.
        """
        meteoswiss_point = self.meteoswiss_point_lookup.find_point(location)
        if meteoswiss_point is not None:
            return ResolvedLocation(meteoswiss_point, None)

        swisstopo_place = self.swisstopo_place_lookup.find_place(location)
        if swisstopo_place is not None:
            nearest_meteoswiss_point = self.meteoswiss_point_lookup.find_nearest_point(swisstopo_place)
            return ResolvedLocation(nearest_meteoswiss_point, swisstopo_place)

        raise CannotAnswerError(
            f"Location '{location}' was not found. Check the spelling, or try a nearby town, village or postal code."
        )
