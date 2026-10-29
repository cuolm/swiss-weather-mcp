"""
Find the places to suggest for a query that is no lookup name, such as "Greina".

The query is the name the user typed, normalised. A name here is a lookup name, see lookup.py.

The suggestions come in two steps:
1. The names that start with the query, such as Davos Dorf and Davos Platz for "Davos".
2. The names spelled like the query, such as Zürich for "Zuerich".

Each step comes biggest first. Each place is suggested once, and at most MAX_SIMILAR_NAMES.
"""
import difflib
from typing import Dict, List

from .place_source import SwisstopoPlace

# What may follow the query at the start of a longer name, such as "davos" in "davos dorf"
NAME_SEPARATORS = (" ", "-", "/", "(", ",")
# How alike two names must be to suggest one for the other, from difflib: 1 is the same name
SIMILAR_NAME_MIN_RATIO = 0.75
MAX_SIMILAR_NAMES = 5


def _list_biggest_places(
    places_by_name: Dict[str, List[SwisstopoPlace]], names: List[str]
) -> List[SwisstopoPlace]:
    """
    Return the biggest place of each name, the places with the most people first.

    The biggest place of a name is the one with the most people, then the one merged from the
    most rows of swissNAMES3D, such as a peak that is also a station.
    """
    places = []
    for name in names:
        biggest_place = places_by_name[name][0]
        places.append(biggest_place)
    return sorted(places, key=lambda place: place.population_band, reverse=True)


def _find_places_starting_with_query(
    places_by_name: Dict[str, List[SwisstopoPlace]], query: str
) -> List[SwisstopoPlace]:
    """Find the places whose name starts with the query as a whole word, such as Davos Dorf for "davos"."""
    names = []
    for name in places_by_name:
        is_longer = len(name) > len(query)
        # A name as short as the query has no character after it to check
        if not is_longer:
            continue

        starts_with_query = name.startswith(query)
        character_after_query = name[len(query)]
        is_whole_word = character_after_query in NAME_SEPARATORS
        if starts_with_query and is_whole_word:
            names.append(name)
    # Places of the same size then come in alphabetical order
    names.sort()
    return _list_biggest_places(places_by_name, names)


def _find_places_spelled_like_query(
    places_by_name: Dict[str, List[SwisstopoPlace]], query: str
) -> List[SwisstopoPlace]:
    """Find the places whose name is spelled like the query, such as Zürich for "zuerich"."""
    names = difflib.get_close_matches(
        query, places_by_name.keys(), n=MAX_SIMILAR_NAMES, cutoff=SIMILAR_NAME_MIN_RATIO,
    )
    return _list_biggest_places(places_by_name, names)


def find_similar_places(
    places_by_name: Dict[str, List[SwisstopoPlace]], query: str
) -> List[SwisstopoPlace]:
    """
    Find the places to suggest for a query that is no lookup name, at most MAX_SIMILAR_NAMES.
    """
    places_starting_with_query = _find_places_starting_with_query(places_by_name, query)
    places_spelled_like_query = _find_places_spelled_like_query(places_by_name, query)

    suggestions: List[SwisstopoPlace] = []
    for place in (
        places_starting_with_query
        + places_spelled_like_query
    ):
        if len(suggestions) == MAX_SIMILAR_NAMES:
            break
        # A place may be under two names, such as Biel/Bienne under "biel" and "bienne"
        if place not in suggestions:
            suggestions.append(place)
    return suggestions
