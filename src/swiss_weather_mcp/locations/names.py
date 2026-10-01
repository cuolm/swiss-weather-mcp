"""Compare place names and positions the same way for the MeteoSwiss points and the swisstopo places."""
import math
import unicodedata
from typing import Protocol


class Position(Protocol):
    """Anything with a position in LV95, the Swiss grid in metres."""

    @property
    def east_m(self) -> float: ...

    @property
    def north_m(self) -> float: ...


def normalise_location(location: str) -> str:
    """Fold case, strip accents and drop periods, so "zurich" finds "Zürich" and "St Moritz" finds "St. Moritz"."""
    # NFKD splits "ü" into "u" and a separate accent mark, which is dropped here
    decomposed = unicodedata.normalize("NFKD", location.casefold())
    without_accents = ""
    for character in decomposed:
        if not unicodedata.combining(character):
            without_accents += character
    # A period becomes a space, so "i.E." and "i. E." are the same once the words are joined by one space
    words = without_accents.replace(".", " ").split()
    return " ".join(words)


def calculate_distance_m(first: Position, second: Position) -> float:
    """Return the distance in metres between two positions."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(first.east_m - second.east_m, first.north_m - second.north_m)
