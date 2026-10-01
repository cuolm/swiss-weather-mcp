"""
Find what a location means: a MeteoSwiss point, or a swisstopo place with its nearest MeteoSwiss point.

How we use it:
1. Look the location up in the MeteoSwiss points, see meteoswiss_points.py.
2. Only when it is not a MeteoSwiss point, look it up in the swisstopo places, see
   swisstopo_places.py, and answer for the MeteoSwiss point nearest to the place.
3. For a name of several swisstopo places, choose the one closest to near, a village or
   postal code next to it. Without near, or with a near too far from all of them, refuse
   and list the villages to call again with:

    location "Blausee", 6 places
      near="Bettmeralp": lake, 2205 m
      near="Blausee-Mitholz": district, 887 m
      ...

4. Refuse a location that neither of them knows.
"""
import logging
import threading
from pathlib import Path
from typing import List, NamedTuple, Optional, Union

import requests

from ..errors import CannotAnswerError
from .meteoswiss_points import MeteoSwissPoint, MeteoSwissPointLookup, MeteoSwissPointSource
from .names import calculate_distance_m
from .swisstopo_places import SwisstopoPlace, SwisstopoPlaceLookup, SwisstopoPlaceSource

logger = logging.getLogger(__name__)

# A place farther than this from near is not the one the user means
MAX_NEAR_DISTANCE_M = 15000


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
        self.swisstopo_place_lookup = SwisstopoPlaceLookup(swisstopo_place_source)

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

    def _build_options_error(self, swisstopo_place_candidates: List[SwisstopoPlace],
                             near: Optional[str]) -> CannotAnswerError:
        """Build the refusal of a name of several places, listing the village to call again with for each."""
        name = swisstopo_place_candidates[0].name
        option_lines = []
        for place in swisstopo_place_candidates:
            village_name = self.meteoswiss_point_lookup.find_nearest_village_name(place)
            option_lines.append(f'  near="{village_name}": {place.object_type_name}, {place.altitude_m:.0f} m')
        listed_options = "\n".join(option_lines)

        no_place_near = ""
        if near is not None:
            no_place_near = f"No '{name}' lies near '{near}'. "
        return CannotAnswerError(
            f"{no_place_near}'{name}' is the name of {len(swisstopo_place_candidates)} places. Call again with "
            f"location '{name}' and near set to one of these villages:\n{listed_options}"
        )

    def _choose_swisstopo_place_near(self, swisstopo_place_candidates: List[SwisstopoPlace],
                                     near: Optional[str]) -> SwisstopoPlace:
        """
        Choose the one of several places with the same name that is closest to near. Raise
        CannotAnswerError with the villages to call again with when there is no near, or when
        no place lies within MAX_NEAR_DISTANCE_M of it.
        """
        if near is None:
            raise self._build_options_error(swisstopo_place_candidates, near)

        # near is looked up in the MeteoSwiss points, not the swisstopo places: a MeteoSwiss name or postal
        # code leads to exactly one point, while many swisstopo names belong to several places themselves
        near_meteoswiss_point = self.meteoswiss_point_lookup.find_point(near)
        if near_meteoswiss_point is None:
            raise self._build_options_error(swisstopo_place_candidates, near)

        closest_swisstopo_place = min(
            swisstopo_place_candidates,
            key=lambda swisstopo_place: calculate_distance_m(swisstopo_place, near_meteoswiss_point),
        )
        if calculate_distance_m(closest_swisstopo_place, near_meteoswiss_point) > MAX_NEAR_DISTANCE_M:
            raise self._build_options_error(swisstopo_place_candidates, near)
        return closest_swisstopo_place

    def find_location(self, location: str, near: Optional[str] = None) -> ResolvedLocation:
        """
        Find what a location means: a MeteoSwiss point, or a swisstopo place with its nearest
        MeteoSwiss point. near is only used for a name of several swisstopo places.

        Case, accents and periods are ignored, and names in other languages such as "Genf" work
        too. Raise CannotAnswerError for an unknown location, and for a name of several places
        that near does not tell apart, listing the villages to call again with.
        """
        meteoswiss_point = self.meteoswiss_point_lookup.find_point(location)
        if meteoswiss_point is not None:
            return ResolvedLocation(meteoswiss_point, None)

        swisstopo_place_candidates = self.swisstopo_place_lookup.find_places(location)
        if not swisstopo_place_candidates:
            raise CannotAnswerError(
                f"Location '{location}' was not found. Check the spelling, or try a nearby town, village or "
                f"postal code."
            )

        if len(swisstopo_place_candidates) == 1:
            swisstopo_place = swisstopo_place_candidates[0]
        else:
            swisstopo_place = self._choose_swisstopo_place_near(swisstopo_place_candidates, near)
        nearest_meteoswiss_point = self.meteoswiss_point_lookup.find_nearest_point(swisstopo_place)
        return ResolvedLocation(nearest_meteoswiss_point, swisstopo_place)
