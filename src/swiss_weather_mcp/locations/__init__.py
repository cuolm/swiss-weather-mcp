"""
Find the place a user means.

    location   what the user typed: a place name or a Swiss postal code
    point      one of the places MeteoSwiss publishes a local forecast for
    place      a named place of swissNAMES3D, the swisstopo collection of geographical names
"""
from .finder import LocationFinder
from .meteoswiss_points import MeteoSwissPoint

__all__ = ["LocationFinder", "MeteoSwissPoint"]
