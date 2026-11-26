"""
Turn a location string, such as "Zürich", "Wil", "Davos" or "Blausee", into the swisstopo place meant.

The location is looked up among the places of swissNAMES3D, see lookup.py:
- One place has the name: that place.
- Several places have the name: nearby_location chooses the one closest to it, within 10 km.
  Without it, the biggest place is taken when it is obvious: it has 1,000 or more people and
  every other place is a settlement of fewer than 100, such as the village Grindelwald and a
  hamlet of that name. A peak, lake or pass among the others has no people and may well be the
  one meant, so it is never passed over. Otherwise, or when no place lies near nearby_location,
  we refuse and list every place, biggest first, so the model can choose with the user. Each
  comes with a settlement to give as near: the nearest settlement of 100 or more people whose
  name leads back to that place. Each also names the municipality and canton it lies in, from
  swissBOUNDARIES3D, see boundary_source.py, so a user who answers with a canton can be matched
  to its line.
- No place has the name, but a municipality has, such as Davos: the biggest settlement inside the
  municipality, from swissBOUNDARIES3D, see boundary_source.py. When several settlements are the
  biggest, of the same population band:
    - close together, they have the same weather, so the first by name is taken: Davos Dorf of
      Davos Dorf and Davos Platz.
    - far apart, we refuse and list them, each with its own name as near, such as the ten villages
      of Anniviers.
- Neither a place nor a municipality has the name: we refuse and suggest up to 10 similar names, see
  suggestions.py, so the model can call again with one of them.

    location
       │
       ▼
    places with that name
       ├── one ──────► that place
       ├── none ─────► a municipality with that name?
       │                 ├── one biggest settlement ─────────────► that settlement
       │                 ├── several biggest, close together ────► the first by name
       │                 ├── several biggest, far apart ─────────► refuse, with one line per settlement:
       │                 │     location="Anniviers", near="Zinal": settlement, 100 to 999 people,
       │                 │       1674 m, municipality Anniviers, canton Valais
       │                 └── no ─► refuse: Location 'Aletschgletscher' was not found. Similar names:
       │                             "Grosser Aletschgletscher" (glacier, 2528 m), ...
       └── several
              │
              ▼
           nearby_location given?
              ├── yes, and a place of the name within 10 km of it ─► the place closest to it
              ├── no, and the biggest place is obvious ──────────────► the biggest place
              └── otherwise ─► refuse, with one line per place, the biggest first:
                           'Blausee' is the name of 6 places. Call again with location
                           'Blausee' and near set to the village next to the place the user
                           means, or ask the user which one they mean. The biggest first:
                             location="Blausee", near="Blausee-Mitholz": quarter and lake,
                               fewer than 20 people, 887 m, municipality Kandergrund, canton Bern
                             location="Blausee", near="Ramiswil": settlement, fewer than 20
                               people, 683 m, municipality Mümliswil-Ramiswil, canton Solothurn
                             ...
                         (starting with "No 'Blausee' lies near 'Davos Dorf'." when one was given,
                         or "No place is called 'Xyzdorf'." when no place has that name)

The tools call nearby_location near, and the texts for the model call a settlement a village.
Two places of a name can lie so close together that every settlement leads to the same one; the
other one then lists the nearest settlement anyway, as both have nearly the same weather.
"""
import heapq
import logging
import math
import threading
from itertools import combinations
from pathlib import Path
from typing import Optional

import requests

from ..distance import calculate_distance_m, is_within_weighted_distance
from ..errors import CannotAnswerError
from .boundary_source import SwisstopoBoundarySource
from .lookup import SwisstopoPlaceLookup
from .place_source import SwisstopoPlace, SwisstopoPlaceSource


logger = logging.getLogger(__name__)

# A place farther than this from nearby_location is not the one the user means
MAX_NEAR_DISTANCE_M = 10000
# How many of the nearest settlements are tried to find one that leads back to a place
MAX_NEAR_SETTLEMENTS_TRIED = 20
# Without near, the biggest place of a name is the one meant when it has 1,000 or more people and every
# other place is a settlement of fewer than 100, so at least ten times smaller
OBVIOUS_PLACE_MIN_BAND = 5
OBVIOUS_OTHER_PLACE_MAX_BAND = 3
# Settlements this close by weighted distance, see calculate_weighted_distance_m, have the same weather
MAX_EQUAL_SETTLEMENT_WEIGHTED_DISTANCE_M = 3000


def _find_obvious_place(places: list[SwisstopoPlace]) -> Optional[SwisstopoPlace]:
    """
    Return the place a name of several places obviously means, or None when it is not obvious.

    That is the biggest place, when it has 1,000 or more people and every other place is a settlement
    of fewer than 100. A place without people, such as a peak, a lake or a pass, may well be the one
    meant, so none is obvious then.
    """
    places_biggest_first = sorted(places, key=lambda place: place.population_band, reverse=True)
    biggest_place = places_biggest_first[0]
    if biggest_place.population_band < OBVIOUS_PLACE_MIN_BAND:
        return None
    for other_place in places_biggest_first[1:]:
        has_no_people = other_place.population_band == 0
        is_too_big = other_place.population_band > OBVIOUS_OTHER_PLACE_MAX_BAND
        if has_no_people or is_too_big:
            return None
    return biggest_place


class PlaceFinder:
    """
    Find the swisstopo place a user means.

    A name of several places needs near, a settlement next to the one the user means, unless one place
    is obvious. A name no place has may be the name of a municipality, which stands for its biggest
    settlements. For an unknown name, suggest similar names, see SwisstopoPlaceLookup.find_places_named_alike.
    """

    def __init__(self, cache_dir: Path):
        place_source = SwisstopoPlaceSource(cache_dir)
        self.place_lookup = SwisstopoPlaceLookup(place_source)
        self.boundary_source = SwisstopoBoundarySource(cache_dir)

    def _load_places(self) -> None:
        """Load the places, or log why not."""
        try:
            self.place_lookup.load_places()
        except requests.RequestException as error:
            logger.warning(f"Could not load the swisstopo places at start, the first question tries again: {error}")

    def _load_boundaries(self) -> None:
        """Load the boundaries, or log why not."""
        try:
            self.boundary_source.load_geopackage()
        except requests.RequestException as error:
            logger.warning(
                f"Could not load the swisstopo boundaries, the lists of places leave out the municipalities: {error}"
            )

    def start_loading_places(self) -> list[threading.Thread]:
        """
        Load the places and boundaries in two background threads and return them, so the first question need not wait.

        The lists of places name the municipality and canton only once the boundaries are
        loaded. The boundaries do not wait for the places, so cached ones are ready at once.
        """
        places_thread = threading.Thread(target=self._load_places, name="load-places", daemon=True)
        boundaries_thread = threading.Thread(target=self._load_boundaries, name="load-boundaries", daemon=True)
        places_thread.start()
        boundaries_thread.start()
        return [places_thread, boundaries_thread]

    def _find_closest_place(
        self, candidate_places: list[SwisstopoPlace], nearby_location_places: list[SwisstopoPlace]
    ) -> Optional[SwisstopoPlace]:
        """Choose the candidate place closest to one of nearby_location_places, or None beyond MAX_NEAR_DISTANCE_M."""
        closest_place = None
        closest_distance_m = math.inf
        # Several places can have the near name too, so we compare every candidate with every one of them
        for nearby_location_place in nearby_location_places:
            for candidate_place in candidate_places:
                distance_m = calculate_distance_m(candidate_place, nearby_location_place)
                if distance_m < closest_distance_m:
                    closest_place = candidate_place
                    closest_distance_m = distance_m
        if closest_distance_m > MAX_NEAR_DISTANCE_M:
            return None
        return closest_place

    def _find_near_settlement_name(self, place: SwisstopoPlace, places: list[SwisstopoPlace]) -> str:
        """
        Name the settlement to give as near for one of several places.

        It is the nearest of the MAX_NEAR_SETTLEMENTS_TRIED nearest settlements that leads back to this
        place, so a model that copies it gets this place; when none does, the nearest settlement.
        """
        nearest_settlements = heapq.nsmallest(
            MAX_NEAR_SETTLEMENTS_TRIED,
            self.place_lookup.near_settlements,
            key=lambda settlement: calculate_distance_m(settlement, place),
        )
        for settlement in nearest_settlements:
            # As the model would give it as near: the name, which other places can have too
            places_of_settlement_name = self.place_lookup.find_places_named(settlement.name)
            if self._find_closest_place(places, places_of_settlement_name) == place:
                return settlement.name
        # Two places of the name lie so close that each settlement leads to the other one;
        # they have nearly the same weather
        return nearest_settlements[0].name

    def _build_option_lines(self, location: str, places: list[SwisstopoPlace], is_municipality: bool) -> list[str]:
        """
        Build one line per place: the call that chooses it, what it is and where it lies.

        The places of a municipality are settlements with names of their own, each given as its own near.
        """
        option_lines = []
        for place in places:
            if is_municipality:
                # The settlements of a municipality have different names, so near can be each one's own name
                near_settlement_name = place.name
            else:
                near_settlement_name = self._find_near_settlement_name(place, places)
            option_line = f'  location="{location}", near="{near_settlement_name}": {place.description}'
            area = self.boundary_source.find_area(place.east_m, place.north_m)
            if area is not None:
                option_line += f", {area.description}"
            option_lines.append(option_line)
        return option_lines

    def _build_options_error(
        self,
        location: str,
        places: list[SwisstopoPlace],
        is_municipality: bool,
        nearby_location: Optional[str],
        nearby_location_places: list[SwisstopoPlace],
    ) -> CannotAnswerError:
        """Build the refusal of a name of several places, listing every place with a settlement to give as near."""
        option_lines = self._build_option_lines(location, places, is_municipality)
        listed_options = "\n".join(option_lines)

        if is_municipality:
            places_sentence = (
                f"'{location}' is a municipality with {len(places)} villages of the same size and different weather."
            )
        else:
            places_sentence = f"'{location}' is the name of {len(places)} places."
        no_place_near_sentence = ""
        if nearby_location is not None:
            if nearby_location_places:
                no_place_near_sentence = f"No '{location}' lies near '{nearby_location}'. "
            else:
                no_place_near_sentence = f"No place is called '{nearby_location}'. "
        return CannotAnswerError(
            f"{no_place_near_sentence}{places_sentence} Call again with location "
            f"'{location}' and near set to the village next to the place the user means, or ask the user "
            f"which one they mean. The biggest first:\n{listed_options}"
        )

    def _build_unknown_name_error(self, location: str) -> CannotAnswerError:
        """Build the refusal of a name no place has, suggesting similar names to call again with."""
        similar_places = self.place_lookup.find_places_named_alike(location)
        if not similar_places:
            return CannotAnswerError(
                f"Location '{location}' was not found. Check the spelling, or try a nearby town or village."
            )
        listed_suggestions = ", ".join(f'"{place.name}" ({place.description})' for place in similar_places)
        return CannotAnswerError(
            f"Location '{location}' was not found. Similar names: {listed_suggestions}. Call again with one of these "
            f"names as location, or ask the user."
        )

    def _find_biggest_settlements_of_municipality(self, location: str) -> list[SwisstopoPlace]:
        """
        Find the biggest settlements of the municipality with this name, sorted by name.

        If there is only one settlement, or several settlements that lie close together and so have the
        same weather, return only the first. Return an empty list if no municipality has this name.
        """
        municipalities = self.boundary_source.find_municipalities_named(location)
        biggest_settlements = []
        for municipality in municipalities:
            biggest_settlements_inside = self.place_lookup.find_biggest_settlements_inside(municipality.polygons)
            biggest_settlements.extend(biggest_settlements_inside)
        settlement_pairs = combinations(biggest_settlements, 2)
        lie_close_together = True
        for first_settlement, second_settlement in settlement_pairs:
            pair_lies_close = is_within_weighted_distance(
                first_settlement, second_settlement, MAX_EQUAL_SETTLEMENT_WEIGHTED_DISTANCE_M
            )
            if not pair_lies_close:
                lie_close_together = False
                break
        if lie_close_together:
            return biggest_settlements[:1]
        return biggest_settlements

    def find_place(self, location: str, nearby_location: Optional[str] = None) -> SwisstopoPlace:
        """
        Find the place a user means, such as Davos Dorf for "Davos".

        Case, accents and periods are ignored. A municipality's name stands for its biggest settlement. When
        several places have the name, nearby_location picks the one closest to it; without it, a town of 1,000
        or more people wins over villages of fewer than 100.

        Raise CannotAnswerError for an unknown name, with similar names, and for several places none of these
        rules picks, with every place to call again with.
        """
        places = self.place_lookup.find_places_named(location)
        if places:
            is_municipality = False
        else:
            places = self._find_biggest_settlements_of_municipality(location)
            is_municipality = True
        if not places:
            raise self._build_unknown_name_error(location)
        if len(places) == 1:
            return places[0]

        nearby_location_places: list[SwisstopoPlace] = []
        if nearby_location is not None:
            nearby_location_places = self.place_lookup.find_places_named(nearby_location)
            closest_place = self._find_closest_place(places, nearby_location_places)
            if closest_place is not None:
                return closest_place
        else:
            # Pick the big town only when the user gave no near. If they gave a near that lies far from every
            # place, they probably mean one of the small ones, so we ask instead of guessing.
            obvious_place = _find_obvious_place(places)
            if obvious_place is not None:
                return obvious_place
        raise self._build_options_error(location, places, is_municipality, nearby_location, nearby_location_places)
