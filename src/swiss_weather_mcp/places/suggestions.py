"""
Find the places to suggest for a query that is no lookup name, such as "Greina".

The query is the name the user typed, normalised. A name here is a lookup name, see lookup.py, such
as "piz ault". A query word is in a name when it is a prefix of one of the name's words, such as
"aul" in "piz ault".

The suggestions come in the order swisstopo's own search uses, from
https://github.com/geoadmin/service-search:
1. The names that start with the query, such as Davos Dorf and Davos Platz for "Davos".
2. The names with all query words in them, such as Piano della Greina for "Greina" and Piz Ault
   for "piz aul".
3. The names spelled like the query, such as Zürich for "Zuerich".

Each step comes biggest first. Each place is suggested once, and at most MAX_SIMILAR_NAMES.
"""
import difflib
import re
from typing import Dict, List

from .place_source import SwisstopoPlace

# What may follow the query at the start of a longer name, such as "davos" in "davos dorf"
NAME_SEPARATORS = (" ", "-", "/", "(", ",")
# What separates the words of a name, such as "Piano della Greina" or "Mümliswil-Ramiswil"
WORD_SEPARATORS = re.compile(r"[ \-/(),']+")
# How alike two names must be to suggest one for the other, from difflib: 1 is the same name
SIMILAR_NAME_MIN_RATIO = 0.75
MAX_SIMILAR_NAMES = 10


def _split_words(name: str) -> List[str]:
    """Split a normalised name into its words, such as ["piano", "della", "greina"]."""
    words = WORD_SEPARATORS.split(name)
    return [word for word in words if word]


def _find_query_words_in_name(name_words: List[str], query_words: List[str]) -> List[str]:
    """Find the query words in a name, those that are a prefix of one of its words, such as "aul" in "piz ault"."""
    query_words_in_name = []
    for query_word in query_words:
        is_in_name = any(name_word.startswith(query_word) for name_word in name_words)
        if is_in_name:
            query_words_in_name.append(query_word)
    return query_words_in_name


def _find_names_with_query_words(
    places_by_name: Dict[str, List[SwisstopoPlace]], query_words: List[str]
) -> Dict[str, List[str]]:
    """Map each name that has at least one query word in it to the query words it has."""
    query_words_by_name = {}
    for name in places_by_name:
        name_words = _split_words(name)
        query_words_in_name = _find_query_words_in_name(name_words, query_words)
        if query_words_in_name:
            query_words_by_name[name] = query_words_in_name
    return query_words_by_name


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


def _find_places_with_all_query_words(
    places_by_name: Dict[str, List[SwisstopoPlace]], query: str
) -> List[SwisstopoPlace]:
    """Find the places whose name has a word starting with each query word, such as Piz Ault for "piz aul"."""
    query_words = _split_words(query)
    query_words_by_name = _find_names_with_query_words(places_by_name, query_words)

    names = []
    for name, query_words_in_name in query_words_by_name.items():
        has_all_query_words = len(query_words_in_name) == len(query_words)
        if has_all_query_words:
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
    places_with_all_query_words = _find_places_with_all_query_words(places_by_name, query)
    places_spelled_like_query = _find_places_spelled_like_query(places_by_name, query)

    suggestions: List[SwisstopoPlace] = []
    for place in (
        places_starting_with_query
        + places_with_all_query_words
        + places_spelled_like_query
    ):
        if len(suggestions) == MAX_SIMILAR_NAMES:
            break
        # A place may be under two names, such as Biel/Bienne under "biel" and "bienne"
        if place not in suggestions:
            suggestions.append(place)
    return suggestions
