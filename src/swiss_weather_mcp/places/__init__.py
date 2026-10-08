"""
Find the place a user means.

    location   what the user typed: the name of a place
    place      one geographic name of swissNAMES3D, the swisstopo collection of the names on
               the national map, with its position: a town, district, lake, peak, pass,
               station or valley. Close rows of the same name are one place.
"""
from .finder import PlaceFinder
from .source import SwisstopoPlace

__all__ = ["PlaceFinder", "SwisstopoPlace"]
