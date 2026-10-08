"""
Turn the location string a tool gets, such as "Zürich", "8001", "Genf" or "Blausee", into a
ResolvedLocation(point, place).

point is always a MeteoSwiss point: a place MeteoSwiss makes a forecast for. place is a
swisstopo place, such as a lake, a peak or a city district, or None.

First we look for the string in the list of MeteoSwiss points: their postal codes, their
names, and their names in other languages, so "Genf" finds Genève. Case, accents and periods
do not matter. If several MeteoSwiss points have the same name, we take the one with the
lowest postal code. If we find a MeteoSwiss point, we are done: point is that MeteoSwiss
point, and place is None, because we never needed the much larger swisstopo list.

Only if the string is not a MeteoSwiss point do we look in the list of swisstopo places.
There we look for places with exactly that name:
- No place has the name: we refuse and ask the user to check the spelling.
- One place has the name: place is that swisstopo place.
- Several places have the name: we can only pick one if nearby_location says which. Otherwise
  we refuse and list every place, so the model can ask the user which one they mean.
Once we have the swisstopo place, point is the MeteoSwiss point nearest to it, because
MeteoSwiss has no forecast for the swisstopo place itself.

The forecast always comes from point. The measurements come from the weather station nearest
to place, or nearest to point when place is None.

    location
       │
       ▼
    found among the MeteoSwiss points? ── yes ──► ResolvedLocation(that MeteoSwiss point, None)
       │ no
       ▼
    swisstopo places with that name
       ├── none ─────► refuse: "Location 'Xyz' was not found. Check the spelling, ..."
       ├── one ──────► ResolvedLocation(its nearest MeteoSwiss point, that swisstopo place)
       └── several
              │
              ▼
           nearby_location given, and found among the MeteoSwiss points?
              ├── no ──► refuse, with one line per swisstopo place:
              │            'Blausee' is the name of 6 places. Call again with location
              │            'Blausee' and near set to one of these:
              │              near="Ramiswil": place, 683 m
              │              near="Bortelhorn": lake, 2462 m
              │              near="Bettmeralp": lake, 2205 m
              │              ...
              └── yes ─► the swisstopo place closest to that MeteoSwiss point, if within 10 km:
                         ResolvedLocation(its nearest MeteoSwiss point, that swisstopo place)
                         (none within 10 km ──► refuse with the same lines, starting with
                          "No 'Blausee' lies near 'Zürich'.")

The tools call nearby_location near. Two swisstopo places with the same name close together
can have the same nearest MeteoSwiss point. That MeteoSwiss point then chooses the swisstopo
place closer to it, a few kilometres from the other, with nearly the same weather.
"""
import heapq
import logging
import math
import threading
from pathlib import Path
from typing import List, NamedTuple, Optional, Union

import requests

from ..errors import CannotAnswerError
from . import lookup, source
from .meteoswiss_points import MeteoSwissPoint, MeteoSwissPointLookup, MeteoSwissPointSource
from .names import calculate_distance_m
from .swisstopo_places import SwisstopoPlace, SwisstopoPlaceLookup, SwisstopoPlaceSource

logger = logging.getLogger(__name__)

# A place farther than this from nearby_location is not the one the user means
MAX_NEAR_DISTANCE_M = 10000
# How many of the nearest villages are tried to find one that leads back to a place
MAX_NEAR_VILLAGES_TRIED = 20


class ResolvedLocation(NamedTuple):
    """The MeteoSwiss point the forecast comes from, and the swisstopo place the user asked about, if any."""
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


class PlaceFinder:
    """
    Find the swisstopo place a user means. A name of several places needs near, a village next
    to the one the user means; for an unknown name, suggest the names that start with it or
    are spelled alike.
    """

    def __init__(self, cache_dir: Path):
        self.place_lookup = lookup.SwisstopoPlaceLookup(source.SwisstopoPlaceSource(cache_dir))

    def _load_places(self) -> None:
        """Load the places, or log why not, so the first question tries again."""
        try:
            self.place_lookup.load_places()
        except requests.RequestException as error:
            logger.warning(f"Could not load the swisstopo places at start, the first question tries again: {error}")

    def start_loading_places(self) -> threading.Thread:
        """
        Load the places in a background thread, so the first question does not wait for their
        download. Return the thread.
        """
        thread = threading.Thread(target=self._load_places, name="load-places", daemon=True)
        thread.start()
        return thread

    def _choose_place_near(
        self, places: List[source.SwisstopoPlace], nearby_location: str
    ) -> Optional[source.SwisstopoPlace]:
        """Choose the place closest to a place called nearby_location, or None when none lies within MAX_NEAR_DISTANCE_M."""
        closest_place = None
        closest_distance_m = math.inf
        # nearby_location can itself be the name of several places, so every pair counts
        for near_place in self.place_lookup.find_places(nearby_location):
            for place in places:
                distance_m = lookup.calculate_distance_m(place, near_place)
                if distance_m < closest_distance_m:
                    closest_place, closest_distance_m = place, distance_m
        if closest_distance_m > MAX_NEAR_DISTANCE_M:
            return None
        return closest_place

    def _find_near_village(self, place: source.SwisstopoPlace, places: List[source.SwisstopoPlace]) -> str:
        """
        Name the village to give as near for one of several places: the nearest one that leads
        back to it, so a model that copies it gets this place.
        """
        nearest_villages = heapq.nsmallest(
            MAX_NEAR_VILLAGES_TRIED,
            self.place_lookup.near_settlements,
            key=lambda village: lookup.calculate_distance_m(village, place),
        )
        for village in nearest_villages:
            if self._choose_place_near(places, village.name) == place:
                return village.name
        # Two places of the name lie so close that each village leads to the other one; they have nearly the same weather
        return nearest_villages[0].name

    def _build_options_error(
        self, location: str, places: List[source.SwisstopoPlace], nearby_location: Optional[str]
    ) -> CannotAnswerError:
        """Build the refusal of a name of several places, listing every place with a village to give as near."""
        option_lines = []
        for place in places:
            option_lines.append(f'  near="{self._find_near_village(place, places)}": {place.description}')
        listed_options = "\n".join(option_lines)

        no_place_near = ""
        if nearby_location is not None:
            no_place_near = f"No '{location}' lies near '{nearby_location}'. "
        return CannotAnswerError(
            f"{no_place_near}'{location}' is the name of {len(places)} places. Call again with location "
            f"'{location}' and near set to the village next to the place the user means, or ask the user "
            f"which one they mean. The biggest first:\n{listed_options}"
        )

    def _build_unknown_name_error(self, location: str) -> CannotAnswerError:
        """Build the refusal of a name no place has, suggesting names that start with it or are spelled alike."""
        similar_places = self.place_lookup.find_similar_names(location)
        if not similar_places:
            return CannotAnswerError(
                f"Location '{location}' was not found. Check the spelling, or try a nearby town or village."
            )
        listed_names = ", ".join(f'"{place.name}" ({place.description})' for place in similar_places)
        return CannotAnswerError(
            f"Location '{location}' was not found. Similar names: {listed_names}. Call again with one of these "
            f"names as location, or ask the user."
        )

    def find_place(self, location: str, nearby_location: Optional[str] = None) -> source.SwisstopoPlace:
        """
        Find the place a user means. nearby_location is only used for a name of several places.

        Case, accents and periods are ignored. Raise CannotAnswerError for an unknown name,
        suggesting similar names, and for a name of several places that nearby_location does not tell
        apart, listing every place to call again with.
        """
        places = self.place_lookup.find_places(location)
        if not places:
            raise self._build_unknown_name_error(location)
        if len(places) == 1:
            return places[0]

        if nearby_location is not None:
            place_near = self._choose_place_near(places, nearby_location)
            if place_near is not None:
                return place_near
        raise self._build_options_error(location, places, nearby_location)
