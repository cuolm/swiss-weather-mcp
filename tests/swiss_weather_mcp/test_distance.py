from datetime import date

from swiss_weather_mcp.distance import rank_by_weighted_distance
from swiss_weather_mcp.measurements.meteoswiss_source import MeteoSwissStation
from swiss_weather_mcp.places.place_source import SwisstopoPlace

BRAUNWALD = SwisstopoPlace(
    name="Braunwald", object_types=("Ort", "Haltestelle Bahn"), population_band=4, is_exonym=False,
    altitude_m=1290.0, east_m=2718807.0, north_m=1199989.0,
)
GLARUS = MeteoSwissStation(
    abbr="GLA", name="Glarus", canton="Glarus", altitude_m=517.0, east_m=2723757.0, north_m=1210567.0,
    data_since=date(1864, 1, 1),
)
ELM = MeteoSwissStation(
    abbr="ELM", name="Elm", canton="Glarus", altitude_m=958.0, east_m=2732266.0, north_m=1198424.0,
    data_since=date(1878, 2, 1),
)


# --- rank_by_weighted_distance ---

def test_rank_by_weighted_distance_puts_the_closest_first():
    # Elm is 13.5 km away and 332 m lower, 20.2 km in all; Glarus 11.7 km away but 773 m lower, 27.1 km
    assert rank_by_weighted_distance(BRAUNWALD, [GLARUS, ELM]) == [ELM, GLARUS]


def test_rank_by_weighted_distance_leaves_out_positions_beyond_the_limit():
    assert rank_by_weighted_distance(BRAUNWALD, [GLARUS, ELM], max_weighted_distance_m=25000) == [ELM]
