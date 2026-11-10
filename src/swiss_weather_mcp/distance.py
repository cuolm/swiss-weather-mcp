"""
Measure how well a station's position suits a place, on LV95, the Swiss grid in metres.

Shared by the MeteoSwiss and the SLF stations, so both networks are compared by one formula.
"""
import math
from typing import Iterable, List, Optional, Protocol, TypeVar

from .places import SwisstopoPlace

# Weather changes more with height than with distance: 1 m of height gap counts like 20 m farther away
METRES_PER_HEIGHT_METRE = 20


class GridPosition(Protocol):
    """A position with an altitude on LV95, such as a station."""

    @property
    def altitude_m(self) -> float: ...

    @property
    def east_m(self) -> float: ...

    @property
    def north_m(self) -> float: ...


# Any kind of position, so ranking SLF stations returns SLF stations
PositionT = TypeVar("PositionT", bound=GridPosition)


def calculate_distance_m(place: SwisstopoPlace, position: GridPosition) -> float:
    """Return the distance in metres between a place and a position."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(place.east_m - position.east_m, place.north_m - position.north_m)


def calculate_weighted_distance_m(place: SwisstopoPlace, position: GridPosition) -> float:
    """Return the distance to a position plus METRES_PER_HEIGHT_METRE times the height gap."""
    height_gap_m = abs(position.altitude_m - place.altitude_m)
    return calculate_distance_m(place, position) + METRES_PER_HEIGHT_METRE * height_gap_m


def rank_by_weighted_distance(
    place: SwisstopoPlace, positions: Iterable[PositionT], max_weighted_distance_m: Optional[float] = None
) -> List[PositionT]:
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
