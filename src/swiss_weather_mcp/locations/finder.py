"""
Find what a location means: a MeteoSwiss point, or a swisstopo place with its nearest MeteoSwiss point.

How we use it:
1. Look the location up in the MeteoSwiss points, see meteoswiss_points.py.
2. Only when it is not a MeteoSwiss point, look it up in the swisstopo places, see
   swisstopo_places.py, and answer for the MeteoSwiss point nearest to the place.
3. A name of several swisstopo places is refused with one option per place, written so
   the lookup accepts it, and named after the nearest MeteoSwiss village:

    "Blausee", 6 places
      "Blausee (lake, 2205 m, near Bettmeralp)"
      "Blausee (district, 887 m, near Blausee-Mitholz)"
      ...

   Such an option, or free text such as "Blausee near Bettmeralp", chooses one place.
4. Refuse a location that neither of them knows.
"""
import logging
import re
import threading
from pathlib import Path
from typing import List, NamedTuple, Optional, Union

import requests

from ..errors import CannotAnswerError
from .meteoswiss_points import MeteoSwissPoint, MeteoSwissPointLookup, MeteoSwissPointSource
from .names import normalise_location
from .swisstopo_places import SwisstopoCandidatePlaces, SwisstopoPlace, SwisstopoPlaceLookup, SwisstopoPlaceSource

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

    def _build_option(self, place: SwisstopoPlace) -> str:
        """Build the text that chooses one of several places, such as "Blausee (lake, 2205 m, near Bettmeralp)"."""
        village_name = self.meteoswiss_point_lookup.find_nearest_village_name(place)
        return f"{place.name} ({place.object_type_name}, {place.altitude_m:.0f} m, near {village_name})"

    def _build_options_error(self, places: List[SwisstopoPlace]) -> CannotAnswerError:
        """Build the refusal of a name of several places, with one option per place."""
        option_lines = []
        for place in places:
            option = self._build_option(place)
            option_lines.append(f'  "{option}"')
        listed_options = "\n".join(option_lines)
        return CannotAnswerError(
            f"'{places[0].name}' is the name of {len(places)} places. Ask again with one of these, written exactly "
            f"as shown:\n{listed_options}"
        )

    def _choose_swisstopo_place_from(self, location: str,
                                     swisstopo_candidate_places: SwisstopoCandidatePlaces) -> SwisstopoPlace:
        """
        Choose one of several places with the same name: by its option, or by the village the
        rest of the text names. Raise CannotAnswerError with the options when neither chooses one.
        """
        normalised_location = normalise_location(location)
        for place in swisstopo_candidate_places.places:
            if normalised_location == normalise_location(self._build_option(place)):
                return place

        for place in swisstopo_candidate_places.places:
            village_name = normalise_location(self.meteoswiss_point_lookup.find_nearest_village_name(place))
            # Whole words only, so a village such as Au is not found inside another word
            if re.search(rf"\b{re.escape(village_name)}\b", swisstopo_candidate_places.rest):
                return place

        raise self._build_options_error(swisstopo_candidate_places.places)

    def find_location(self, location: str) -> ResolvedLocation:
        """
        Find what a location means: a MeteoSwiss point, or a swisstopo place with its nearest
        MeteoSwiss point.

        Case, accents and periods are ignored, and names in other languages such as "Genf" work
        too. Raise CannotAnswerError for an unknown location, and for a name of several places,
        listing them as options to ask again with.
        """
        meteoswiss_point = self.meteoswiss_point_lookup.find_point(location)
        if meteoswiss_point is not None:
            return ResolvedLocation(meteoswiss_point, None)

        swisstopo_candidate_places = self.swisstopo_place_lookup.find_candidate_places(location)
        if not swisstopo_candidate_places.places:
            raise CannotAnswerError(
                f"Location '{location}' was not found. Check the spelling, or try a nearby town, village or "
                f"postal code."
            )

        if len(swisstopo_candidate_places.places) == 1:
            swisstopo_place = swisstopo_candidate_places.places[0]
        else:
            swisstopo_place = self._choose_swisstopo_place_from(location, swisstopo_candidate_places)
        nearest_meteoswiss_point = self.meteoswiss_point_lookup.find_nearest_point(swisstopo_place)
        return ResolvedLocation(nearest_meteoswiss_point, swisstopo_place)
