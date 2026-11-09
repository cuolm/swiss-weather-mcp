"""
Measure how well a station's position suits a place, on LV95, the Swiss grid in metres.

Shared by the MeteoSwiss and the SLF stations, so both networks are compared by one formula.
"""
import math
from typing import Protocol

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


def calculate_distance_m(place: SwisstopoPlace, position: GridPosition) -> float:
    """Return the distance in metres between a place and a position."""
    # LV95 is a flat grid in metres, so Pythagoras is exact enough within Switzerland
    return math.hypot(place.east_m - position.east_m, place.north_m - position.north_m)


def calculate_weighted_distance_m(place: SwisstopoPlace, position: GridPosition) -> float:
    """Return the distance to a position plus METRES_PER_HEIGHT_METRE times the height gap."""
    height_gap_m = abs(position.altitude_m - place.altitude_m)
    return calculate_distance_m(place, position) + METRES_PER_HEIGHT_METRE * height_gap_m
