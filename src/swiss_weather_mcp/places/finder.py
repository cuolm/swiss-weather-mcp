"""
Turn the location string a tool gets, such as "Zürich", "Wil", "Davos" or "Blausee", into the
swisstopo place the user means.

The location is looked up among the places of swissNAMES3D, see lookup.py:
- One place has the name: that place.
- Several places have the name: nearby_location chooses the one closest to it, within 10 km.
  Without it, or when no place lies near it, we refuse and list every place, biggest first,
  so the model can choose with the user. Each comes with a village to give as near: the
  nearest village of 100 or more people whose name leads back to that place.
- No place has the name: we refuse and suggest the names that start with it or are spelled
  alike, so the model can call again with one of them.

    location
       │
       ▼
    places with that name
       ├── one ──────► that place
       ├── none ─────► refuse: Location 'Davos' was not found. Similar names: "Davos Dorf"
       │               (settlement, 2,000 to 9,999 people, 1564 m), "Davos Platz" (...), ...
       └── several
              │
              ▼
           nearby_location given, and a place of the name within 10 km of it?
              ├── yes ─► the place closest to it
              └── no ──► refuse, with one line per place, the biggest first:
                           'Blausee' is the name of 6 places. Call again with location
                           'Blausee' and near set to the village next to the place the user
                           means, or ask the user which one they mean. The biggest first:
                             near="Blausee-Mitholz": district and lake, fewer than 20 people, 887 m
                             near="Ramiswil": settlement, fewer than 20 people, 683 m
                             ...
                         (starting with "No 'Blausee' lies near 'Kandertal'." when one was given)

The tools call nearby_location near. Two places of a name can lie so close together that every
village leads to the same one; the other one then lists the nearest village anyway, as both
have nearly the same weather.
"""
import heapq
import logging
import math
import threading
from pathlib import Path
from typing import List, Optional

import requests

from ..errors import CannotAnswerError
from .lookup import SwisstopoPlaceLookup, calculate_distance_m
from .source import SwisstopoPlace, SwisstopoPlaceSource

logger = logging.getLogger(__name__)

# A place farther than this from nearby_location is not the one the user means
MAX_NEAR_DISTANCE_M = 10000
# How many of the nearest villages are tried to find one that leads back to a place
MAX_NEAR_VILLAGES_TRIED = 20


class PlaceFinder:
    """
    Find the swisstopo place a user means. A name of several places needs near, a village next
    to the one the user means; for an unknown name, suggest the names that start with it or
    are spelled alike.
    """

    def __init__(self, cache_dir: Path):
        self.place_lookup = SwisstopoPlaceLookup(SwisstopoPlaceSource(cache_dir))

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

    def _choose_place_near(self, places: List[SwisstopoPlace], nearby_location: str) -> Optional[SwisstopoPlace]:
        """Choose the place closest to a place called nearby_location, or None when none lies within MAX_NEAR_DISTANCE_M."""
        closest_place = None
        closest_distance_m = math.inf
        # nearby_location can itself be the name of several places, so every pair counts
        for near_place in self.place_lookup.find_places(nearby_location):
            for place in places:
                distance_m = calculate_distance_m(place, near_place)
                if distance_m < closest_distance_m:
                    closest_place, closest_distance_m = place, distance_m
        if closest_distance_m > MAX_NEAR_DISTANCE_M:
            return None
        return closest_place

    def _find_near_village(self, place: SwisstopoPlace, places: List[SwisstopoPlace]) -> str:
        """
        Name the village to give as near for one of several places: the nearest one that leads
        back to it, so a model that copies it gets this place.
        """
        nearest_villages = heapq.nsmallest(
            MAX_NEAR_VILLAGES_TRIED,
            self.place_lookup.near_settlements,
            key=lambda village: calculate_distance_m(village, place),
        )
        for village in nearest_villages:
            if self._choose_place_near(places, village.name) == place:
                return village.name
        # Two places of the name lie so close that each village leads to the other one; they have nearly the same weather
        return nearest_villages[0].name

    def _build_options_error(
        self, location: str, places: List[SwisstopoPlace], nearby_location: Optional[str]
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

    def find_place(self, location: str, nearby_location: Optional[str] = None) -> SwisstopoPlace:
        """
        Find the place a user means. nearby_location is only used for a name of several places.

        Case, accents and periods are ignored. Raise CannotAnswerError for an unknown name,
        suggesting similar names, and for a name of several places that nearby_location does not
        tell apart, listing every place to call again with.
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
