from swiss_weather_mcp.polygons import polygons_contain, ring_contains

# A square with a square hole, as a list of polygons, each its outer ring followed by its holes
SQUARE_WITH_HOLE = [[
    [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)],
    [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0), (4.0, 4.0)],
]]
# A diamond with its east and west corners at north 5, on the line through a position at that height
DIAMOND = [(5.0, 0.0), (10.0, 5.0), (5.0, 10.0), (0.0, 5.0), (5.0, 0.0)]


# --- ring_contains ---

def test_ring_contains_a_position_level_with_a_corner():
    # The two edges meeting at the east corner count as one crossing, not two
    assert ring_contains(DIAMOND, 2.0, 5.0)


def test_ring_contains_not_a_position_level_with_two_corners():
    # The line passes through both corners, east of the position: two crossings, outside
    assert not ring_contains(DIAMOND, -2.0, 5.0)


# --- polygons_contain ---

def test_polygons_contain_a_position_inside():
    assert polygons_contain(SQUARE_WITH_HOLE, 2.0, 2.0)


def test_polygons_contain_not_a_position_in_a_hole():
    assert not polygons_contain(SQUARE_WITH_HOLE, 5.0, 5.0)


def test_polygons_contain_not_a_position_outside():
    assert not polygons_contain(SQUARE_WITH_HOLE, 12.0, 5.0)
