import pytest

from swiss_weather_mcp.coordinates import calculate_lv95_east_m, calculate_lv95_north_m

ARCSECONDS_PER_DEGREE = 3600


# --- calculate_lv95_east_m and calculate_lv95_north_m ---

def test_calculate_lv95_east_m_and_north_m_of_the_swisstopo_example():
    # The numerical example of section 1 of the swisstopo formulas: 46° 02' 38.87", 8° 43' 49.79"
    latitude_deg = 46 + 2 / 60 + 38.87 / ARCSECONDS_PER_DEGREE
    longitude_deg = 8 + 43 / 60 + 49.79 / ARCSECONDS_PER_DEGREE
    assert calculate_lv95_east_m(latitude_deg, longitude_deg) == pytest.approx(2699999.76, abs=0.01)
    assert calculate_lv95_north_m(latitude_deg, longitude_deg) == pytest.approx(1099999.97, abs=0.01)
