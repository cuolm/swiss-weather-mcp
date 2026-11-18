"""Measure how well a station's position suits a place, on LV95, the Swiss grid in metres."""
import math
from collections.abc import Iterable
from typing import Optional, Protocol, TypeVar


# Weather changes more with height than with distance: 1 m of height gap counts like 20 m farther away
METRES_PER_HEIGHT_METRE = 20
METRES_PER_KILOMETRE = 1000
# A station or forecast point this close stands for the place itself, so the answers do not name it in
# data_from. 50 m of height changes the temperature by about 0.3 °C.
CLOSE_MAX_DISTANCE_KM = 1.0
CLOSE_MAX_HEIGHT_DIFFERENCE_M = 50
# How many of the closest stations a tool tries before it gives up, so a question downloads only a few files
MAX_STATIONS_TRIED = 3


class GridPosition(Protocol):
    """A position with an altitude on LV95, such as a place or a station."""

    @property
    def altitude_m(self) -> float:
        """The altitude in metres above sea level."""

    @property
    def east_m(self) -> float:
        """The LV95 east coordinate in metres."""

    @property
    def north_m(self) -> float:
        """The LV95 north coordinate in metres."""


# Any kind of position, so ranking SLF stations returns SLF stations
PositionT = TypeVar("PositionT", bound=GridPosition)


def calculate_distance_m(first: GridPosition, second: GridPosition) -> float:
    """Return the distance in metres between two positions."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(first.east_m - second.east_m, first.north_m - second.north_m)


def calculate_weighted_distance_m(place: GridPosition, position: GridPosition) -> float:
    """Return the distance to a position plus METRES_PER_HEIGHT_METRE times the height gap."""
    height_gap_m = abs(position.altitude_m - place.altitude_m)
    return calculate_distance_m(place, position) + METRES_PER_HEIGHT_METRE * height_gap_m


def is_close(place: GridPosition, position: GridPosition) -> bool:
    """Tell whether a position is within CLOSE_MAX_DISTANCE_KM and CLOSE_MAX_HEIGHT_DIFFERENCE_M of a place."""
    # Rounded as the answers show them, to 0.1 km and 1 m
    distance_km = round(calculate_distance_m(place, position) / METRES_PER_KILOMETRE, 1)
    difference_m = round(position.altitude_m - place.altitude_m)
    return distance_km <= CLOSE_MAX_DISTANCE_KM and abs(difference_m) <= CLOSE_MAX_HEIGHT_DIFFERENCE_M


def rank_by_weighted_distance(
    place: GridPosition, positions: Iterable[PositionT], max_weighted_distance_m: Optional[float] = None
) -> list[PositionT]:
    """
    Rank positions by their weighted distance to a place, the closest first.

    With max_weighted_distance_m, leave out the positions farther away than it.
    """
    close_positions = []
    for position in positions:
        weighted_distance_m = calculate_weighted_distance_m(place, position)
        if max_weighted_distance_m is None or weighted_distance_m <= max_weighted_distance_m:
            close_positions.append(position)
    return sorted(close_positions, key=lambda position: calculate_weighted_distance_m(place, position))
