"""
Tell whether a position lies inside polygons with holes, on LV95, the Swiss grid in metres.

The crossing number algorithm, or even-odd rule: a line from the position crosses the edges of a ring an
odd number of times when the position lies inside, https://en.wikipedia.org/wiki/Point_in_polygon

    A polygon: its outer ring and, rarely, holes, each a closed list of corners

            ┌───────────────────────────────┐
            │   A ●─────────────────────────┼──►   A: outer ring 1 crossing (odd): inside
            │            ┌──────────┐       │
            │            │ B ●──────┼───────┼──►   B: outer ring 1 (odd), hole 1 (odd): in the hole, outside
            │            └──────────┘       │
      C ●───┼───────────────────────────────┼──►   C: outer ring 2 crossings (even): outside
            └───────────────────────────────┘

The line runs east from the position, and the crossings are counted for each ring alone. A position is
inside the polygon when it is inside the outer ring and in none of its holes.
"""
from typing import List, Optional, Tuple

# A corner is (east, north) in metres, a ring a closed list of corners, and a polygon its outer ring
# followed by its holes
Corner = Tuple[float, float]
Ring = List[Corner]
Polygon = List[Ring]


def _find_crossing_east(edge_start: Corner, edge_end: Corner, horizontal_line_north: float) -> Optional[float]:
    """Return the east coordinate where an edge crosses a horizontal line, or None when the edge stays above or below it."""
    edge_start_east, edge_start_north = edge_start
    edge_end_east, edge_end_north = edge_end
    # The edge crosses the line only when one of its corners lies north of it and the other does not
    edge_start_is_north_of_line = edge_start_north > horizontal_line_north
    edge_end_is_north_of_line = edge_end_north > horizontal_line_north
    if edge_start_is_north_of_line == edge_end_is_north_of_line:
        return None
    # How far along the edge it meets the line, from 0 at its start to 1 at its end
    share_of_edge = (horizontal_line_north - edge_start_north) / (edge_end_north - edge_start_north)
    return edge_start_east + share_of_edge * (edge_end_east - edge_start_east)


def ring_contains(ring: Ring, position_east_m: float, position_north_m: float) -> bool:
    """Tell whether a position lies inside a closed ring: a line from it to the east crosses an odd number of edges."""
    crossings_east_of_position = 0
    # Each edge runs from one corner to the next; a closed ring ends at its first corner
    for edge_start, edge_end in zip(ring, ring[1:]):
        crossing_east_m = _find_crossing_east(edge_start, edge_end, position_north_m)
        if crossing_east_m is not None and crossing_east_m > position_east_m:
            crossings_east_of_position += 1
    return crossings_east_of_position % 2 == 1


def polygons_contain(polygons: List[Polygon], position_east_m: float, position_north_m: float) -> bool:
    """Tell whether a position lies inside one of the polygons: inside its outer ring and in none of its holes."""
    for outer_ring, *holes in polygons:
        if not ring_contains(outer_ring, position_east_m, position_north_m):
            continue
        is_in_a_hole = any(ring_contains(hole, position_east_m, position_north_m) for hole in holes)
        if not is_in_a_hole:
            return True
    return False
