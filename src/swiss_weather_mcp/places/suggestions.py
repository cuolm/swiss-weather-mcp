"""
Find the places to suggest for a query that is no lookup name, such as "Greina".

The query is the name the user typed, normalised. A name here is a lookup name, see lookup.py, such
as "piz ault". A name contains a query word when one of its words starts with it, such as "piz ault"
containing "aul".

The suggestions come in the order swisstopo's own search uses, from
https://github.com/geoadmin/service-search:
1. The names that start with the query, such as Davos Dorf and Davos Platz for "Davos".
2. The names containing all query words, such as Piano della Greina for "Greina" and Piz Ault
   for "piz aul".
3. The names spelled like the query, such as Zürich for "Zuerich".
4. Our own addition, which swisstopo's search does not have: when places are still free, the
   names containing some query words, such as Piz Greina for "Greina plateau". Those containing
   more query words come first, then those containing the rarer query word, the idea of the
   inverse document frequency in BM25 (https://en.wikipedia.org/wiki/Okapi_BM25): "greina" is
   in 8 names, "plateau" in 13.

Steps 1 to 3 each come biggest first. Each place is suggested once, and at most MAX_SIMILAR_NAMES.
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


def _find_names_containing_query_words(
    places_by_name: Dict[str, List[SwisstopoPlace]], query_words: List[str]
) -> Dict[str, List[str]]:
    """Map each name containing at least one query word to the query words it contains."""
    contained_query_words_by_name = {}
    for name in places_by_name:
        name_words = _split_words(name)
        query_words_contained_in_name = []
        for query_word in query_words:
            is_contained_in_name = any(name_word.startswith(query_word) for name_word in name_words)
            if is_contained_in_name:
                query_words_contained_in_name.append(query_word)
        if query_words_contained_in_name:
            contained_query_words_by_name[name] = query_words_contained_in_name
    return contained_query_words_by_name


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
    names_starting_with_query = []
    for name in places_by_name:
        is_whole_word_start = any(name.startswith(query + separator) for separator in NAME_SEPARATORS)
        if is_whole_word_start:
            names_starting_with_query.append(name)
    names_starting_with_query.sort()
    return _list_biggest_places(places_by_name, names_starting_with_query)


def _find_places_containing_all_query_words(
    places_by_name: Dict[str, List[SwisstopoPlace]], query: str
) -> List[SwisstopoPlace]:
    """Find the places whose name contains all query words, such as Piz Ault for "piz aul"."""
    query_words = _split_words(query)
    contained_query_words_by_name = _find_names_containing_query_words(places_by_name, query_words)

    names_containing_all_query_words = []
    for name, query_words_contained_in_name in contained_query_words_by_name.items():
        contains_all_query_words = len(query_words_contained_in_name) == len(query_words)
        if contains_all_query_words:
            names_containing_all_query_words.append(name)
    names_containing_all_query_words.sort()
    return _list_biggest_places(places_by_name, names_containing_all_query_words)


def _find_places_spelled_like_query(
    places_by_name: Dict[str, List[SwisstopoPlace]], query: str
) -> List[SwisstopoPlace]:
    """Find the places whose name is spelled like the query, such as Zürich for "zuerich"."""
    names_spelled_like_query = difflib.get_close_matches(
        query, places_by_name.keys(), n=MAX_SIMILAR_NAMES, cutoff=SIMILAR_NAME_MIN_RATIO,
    )
    return _list_biggest_places(places_by_name, names_spelled_like_query)


def _find_places_containing_some_query_words(
    places_by_name: Dict[str, List[SwisstopoPlace]], query: str
) -> List[SwisstopoPlace]:
    """
    Find the places whose name contains at least one query word, such as Piz Greina for "greina plateau".

    Those containing more query words come first, then those containing the rarer query word, the one
    fewer names contain, then the biggest. Empty for one query word, which
    _find_places_containing_all_query_words covers.
    """
    query_words = _split_words(query)
    if len(query_words) <= 1:
        return []

    contained_query_words_by_name = _find_names_containing_query_words(places_by_name, query_words)

    name_count_by_query_word = dict.fromkeys(query_words, 0)
    for query_words_contained_in_name in contained_query_words_by_name.values():
        for query_word in query_words_contained_in_name:
            name_count_by_query_word[query_word] += 1

    ranked_places = []
    for name in sorted(contained_query_words_by_name):
        query_words_contained_in_name = contained_query_words_by_name[name]
        name_counts = []
        for query_word in query_words_contained_in_name:
            name_counts.append(name_count_by_query_word[query_word])
        smallest_name_count = min(name_counts)
        place = places_by_name[name][0]
        rank = (-len(query_words_contained_in_name), smallest_name_count, -place.population_band)
        ranked_places.append((rank, place))
    ranked_places.sort(key=lambda ranked_place: ranked_place[0])
    return [place for _, place in ranked_places]


def find_similar_places(
    places_by_name: Dict[str, List[SwisstopoPlace]], query: str
) -> List[SwisstopoPlace]:
    """
    Find the places to suggest for a query that is no lookup name, at most MAX_SIMILAR_NAMES.
    """
    places_starting_with_query = _find_places_starting_with_query(places_by_name, query)
    places_containing_all_query_words = _find_places_containing_all_query_words(places_by_name, query)
    places_spelled_like_query = _find_places_spelled_like_query(places_by_name, query)
    places_containing_some_query_words = _find_places_containing_some_query_words(places_by_name, query)

    suggestions: List[SwisstopoPlace] = []
    for place in (
        places_starting_with_query
        + places_containing_all_query_words
        + places_spelled_like_query
        + places_containing_some_query_words
    ):
        if len(suggestions) == MAX_SIMILAR_NAMES:
            break
        # A place may be under two names, such as Biel/Bienne under "biel" and "bienne"
        if place not in suggestions:
            suggestions.append(place)
    return suggestions
