"""
Find what a location means, returned as a ResolvedLocation(point, place): point is the
MeteoSwiss point the forecast and the measurements come from, and place is the swisstopo
place the user asked about, or None when the location is a MeteoSwiss point itself.

    location
       │
       ▼
    a MeteoSwiss point? ── yes ──► ResolvedLocation(that MeteoSwiss point, None)
       │ no
       ▼
    swisstopo places with that name
       ├── none ─────► refuse: "Location 'Xyz' was not found. Check the spelling, ..."
       ├── one ──────► ResolvedLocation(its nearest MeteoSwiss point, that swisstopo place)
       └── several
              │
              ▼
           nearby_location given, and a MeteoSwiss name or postal code?
              ├── no ──► refuse, with one line per place:
              │            'Blausee' is the name of 6 places. Call again with location
              │            'Blausee' and near set to one of these:
              │              near="Ramiswil": place, 683 m
              │              near="Bortelhorn": lake, 2462 m
              │              near="Bettmeralp": lake, 2205 m
              │              ...
              └── yes ─► the swisstopo place closest to that point, if within 10 km:
                         ResolvedLocation(its nearest MeteoSwiss point, that swisstopo place)
                         (none within 10 km ──► refuse with the same lines, starting with
                          "No 'Blausee' lies near 'Zürich'.")

The tools call nearby_location near. Two places with the same name close together can have
the same nearest MeteoSwiss point. That point then chooses the one closer to it, a few
kilometres from the other, with nearly the same weather.
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

# A place farther than this from nearby_location is not the one the user means
MAX_NEAR_DISTANCE_M = 10000


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

    def _build_options_error(
        self, swisstopo_place_candidates: List[SwisstopoPlace], nearby_location: Optional[str]
    ) -> CannotAnswerError:
        """Build the refusal of a name of several places, listing each with its nearest MeteoSwiss point as near."""
        name = swisstopo_place_candidates[0].name
        option_lines = []
        for swisstopo_place in swisstopo_place_candidates:
            nearest_meteoswiss_point = self.meteoswiss_point_lookup.find_nearest_point(swisstopo_place)
            description = f"{swisstopo_place.object_type_name}, {swisstopo_place.altitude_m:.0f} m"
            option_lines.append(f'  near="{nearest_meteoswiss_point.name}": {description}')
        listed_options = "\n".join(option_lines)

        no_place_near = ""
        if nearby_location is not None:
            no_place_near = f"No '{name}' lies near '{nearby_location}'. "
        return CannotAnswerError(
            f"{no_place_near}'{name}' is the name of {len(swisstopo_place_candidates)} places. Call again with "
            f"location '{name}' and near set to one of these:\n{listed_options}"
        )

    def _choose_swisstopo_place_closest_to_meteoswiss_point(
        self,
        swisstopo_place_candidates: List[SwisstopoPlace],
        nearby_meteoswiss_point: MeteoSwissPoint,
        nearby_location: str,
    ) -> SwisstopoPlace:
        """
        Choose the place closest to a MeteoSwiss point. Raise CannotAnswerError with the options
        when every place lies farther than MAX_NEAR_DISTANCE_M from it.
        """
        closest_swisstopo_place = min(
            swisstopo_place_candidates,
            key=lambda swisstopo_place: calculate_distance_m(swisstopo_place, nearby_meteoswiss_point),
        )
        if calculate_distance_m(closest_swisstopo_place, nearby_meteoswiss_point) > MAX_NEAR_DISTANCE_M:
            raise self._build_options_error(swisstopo_place_candidates, nearby_location)
        return closest_swisstopo_place

    def _choose_swisstopo_place_closest_to_nearby_location(
        self, swisstopo_place_candidates: List[SwisstopoPlace], nearby_location: Optional[str]
    ) -> SwisstopoPlace:
        """
        Choose the one of several places with the same name that is closest to nearby_location.
        Raise CannotAnswerError with the options when there is no nearby_location, or when it is
        not a MeteoSwiss name or postal code.
        """
        if nearby_location is None:
            raise self._build_options_error(swisstopo_place_candidates, nearby_location)

        # A MeteoSwiss name or postal code leads to one point, while a swisstopo name can belong to several places
        nearby_meteoswiss_point = self.meteoswiss_point_lookup.find_point(nearby_location)
        if nearby_meteoswiss_point is None:
            raise self._build_options_error(swisstopo_place_candidates, nearby_location)
        return self._choose_swisstopo_place_closest_to_meteoswiss_point(
            swisstopo_place_candidates, nearby_meteoswiss_point, nearby_location
        )

    def find_location(self, location: str, nearby_location: Optional[str] = None) -> ResolvedLocation:
        """
        Find what a location means: a MeteoSwiss point, or a swisstopo place with its nearest
        MeteoSwiss point. nearby_location is only used for a name of several swisstopo places.

        Case, accents and periods are ignored, and names in other languages such as "Genf" work
        too. Raise CannotAnswerError for an unknown location, and for a name of several places
        that nearby_location does not tell apart, listing each place to call again with.
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
            swisstopo_place = self._choose_swisstopo_place_closest_to_nearby_location(
                swisstopo_place_candidates, nearby_location
            )
        nearest_meteoswiss_point = self.meteoswiss_point_lookup.find_nearest_point(swisstopo_place)
        return ResolvedLocation(nearest_meteoswiss_point, swisstopo_place)
