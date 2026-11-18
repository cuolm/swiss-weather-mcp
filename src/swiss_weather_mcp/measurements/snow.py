"""Build the measurements_snow answer: the snow depth and the new snow for a place, each from its own station."""
import logging
from collections.abc import Sequence
from typing import Optional, TypedDict

import requests

from ..distance import MAX_STATIONS_TRIED, is_close, rank_by_weighted_distance
from ..errors import CannotAnswerError
from ..formatting import DataFromField, describe_distance_and_height, describe_source, format_swiss_time, round_to_tenth
from ..places import SwisstopoPlace
from ..swiss_time import SWISS_TZ
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStation
from .slf_source import SLFMeasurementSource, SLFStation
from .snow_values import LatestNewSnow, LatestSnowDepth, SnowStation


logger = logging.getLogger(__name__)

# Snow changes with height and terrain, so a station farther than this by distance plus 20 times the
# height gap, such as 15 km away and 500 m higher, says too little about a place. Tested on the 5817
# forecast points: 94 % of those between 1200 and 2000 m keep a new snow station, and 88 % of those below
# 800 m, where the nearest SLF station is often 40 km away and 600 m higher, get none.
MAX_SNOW_STATION_WEIGHTED_DISTANCE_M = 25000


class SnowDepthBlock(DataFromField):
    """The snow_depth block of the measurements_snow answer."""
    measured_at: str
    snow_depth_cm: Optional[float]


class NewSnowBlock(DataFromField):
    """The new_snow block of the measurements_snow answer."""
    measured_at: str
    new_snow_24_hours_cm: Optional[float]
    new_snow_3_days_cm: Optional[float]


class SnowAnswer(TypedDict):
    """The measurements_snow answer; a block is None when no station close enough has a current value."""
    place: str
    snow_depth: Optional[SnowDepthBlock]
    new_snow: Optional[NewSnowBlock]
    source: str


def list_slf_snow_stations(slf_source: SLFMeasurementSource) -> list[SLFStation]:
    """List the SLF snow stations, or none when SLF cannot be reached."""
    try:
        return slf_source.list_snow_stations()
    except requests.RequestException as error:
        logger.warning(f"Could not download the SLF snow stations, answering without them: {error}")
        return []


def _read_snow_depth(
    meteoswiss_source: MeteoSwissMeasurementSource, slf_source: SLFMeasurementSource, station: SnowStation
) -> Optional[LatestSnowDepth]:
    """Read a station's current snow depth from its source, or None when it has none or SLF cannot be reached."""
    if isinstance(station, MeteoSwissStation):
        return meteoswiss_source.read_snow_depth(station)
    try:
        return slf_source.read_snow_depth(station)
    except requests.RequestException as error:
        logger.warning(f"Could not download the snow depth of SLF station {station.code}: {error}")
        return None


def _find_snow_depth(
    meteoswiss_source: MeteoSwissMeasurementSource,
    slf_source: SLFMeasurementSource,
    place: SwisstopoPlace,
    snow_stations: list[SnowStation],
) -> Optional[LatestSnowDepth]:
    """
    Find the current snow depth for a place from the given snow stations.

    The stations within MAX_SNOW_STATION_WEIGHTED_DISTANCE_M are ranked by distance plus
    METRES_PER_HEIGHT_METRE times the height gap, and the first of the first MAX_STATIONS_TRIED
    with a current snow depth is taken.
    """
    ranked_snow_stations = rank_by_weighted_distance(place, snow_stations, MAX_SNOW_STATION_WEIGHTED_DISTANCE_M)
    for station in ranked_snow_stations[:MAX_STATIONS_TRIED]:
        snow_depth = _read_snow_depth(meteoswiss_source, slf_source, station)
        if snow_depth is not None:
            return snow_depth
        logger.info(f"Station {station.name} has no current snow depth, trying the next station")
    return None


def _read_new_snow(slf_source: SLFMeasurementSource, station: SLFStation) -> Optional[LatestNewSnow]:
    """Read an SLF station's new snow, or None when it has none or SLF cannot be reached."""
    try:
        return slf_source.read_new_snow(station)
    except requests.RequestException as error:
        logger.warning(f"Could not download the new snow of SLF station {station.code}: {error}")
        return None


def _find_new_snow(
    slf_source: SLFMeasurementSource, place: SwisstopoPlace, slf_snow_stations: list[SLFStation]
) -> Optional[LatestNewSnow]:
    """
    Find the new snow for a place from the given SLF snow stations, the only ones that have it.

    The stations are chosen and ranked as for the snow depth, and the first of the first
    MAX_STATIONS_TRIED with new snow from today or yesterday is taken.
    """
    ranked_snow_stations = rank_by_weighted_distance(place, slf_snow_stations, MAX_SNOW_STATION_WEIGHTED_DISTANCE_M)
    for station in ranked_snow_stations[:MAX_STATIONS_TRIED]:
        new_snow = _read_new_snow(slf_source, station)
        if new_snow is not None:
            return new_snow
        logger.info(f"SLF station {station.code} has no current new snow, trying the next station")
    return None


def describe_closest_snow_station(place: SwisstopoPlace, snow_stations: Sequence[SnowStation]) -> str:
    """Say that no snow station is close enough to a place, and which one is the closest."""
    ranked_snow_stations = rank_by_weighted_distance(place, snow_stations)
    closest_station = ranked_snow_stations[0]
    distance_and_height = describe_distance_and_height(place, closest_station)
    return (
        f"No snow station is close enough to {place.display_name} in distance and height. The closest, "
        f"{closest_station.display_name}, is {distance_and_height}."
    )


def describe_snow_station(place: SwisstopoPlace, station: SnowStation, value_name: str) -> str:
    """Say which station a snow value of a place comes from, how far away and how much higher or lower."""
    distance_and_height = describe_distance_and_height(place, station)
    if isinstance(station, MeteoSwissStation) and station.is_manual_site:
        source = f"was measured by an observer at the MeteoSwiss station {station.display_name}"
    elif isinstance(station, MeteoSwissStation):
        source = f"comes from the MeteoSwiss station {station.display_name}"
    elif station.is_manual_site:
        source = f"was measured by an observer at {station.display_name}"
    else:
        source = f"comes from the automatic SLF station {station.display_name}"
    return f"The {value_name} for {place.display_name} {source}, {distance_and_height}."


def _build_snow_depth_block(place: SwisstopoPlace, snow_depth: LatestSnowDepth) -> SnowDepthBlock:
    """Build the snow_depth block of the snow answer."""
    data_from_field: DataFromField = {}
    # A station next to the place stands for it, so the block does not name it
    if not is_close(place, snow_depth.station):
        station_sentence = describe_snow_station(place, snow_depth.station, "snow depth")
        swiss_measured_at = snow_depth.measured_at.astimezone(SWISS_TZ)
        data_from_field = {
            "data_from": (
                f"{station_sentence} It was measured at {swiss_measured_at:%H:%M} on {swiss_measured_at:%Y-%m-%d}."
            ),
        }
    return {
        **data_from_field,
        "measured_at": format_swiss_time(snow_depth.measured_at),
        "snow_depth_cm": round_to_tenth(snow_depth.snow_depth_cm),
    }


def _build_new_snow_block(place: SwisstopoPlace, new_snow: LatestNewSnow) -> NewSnowBlock:
    """Build the new_snow block of the snow answer."""
    data_from_field: DataFromField = {}
    # A station next to the place stands for it, so the block does not name it
    if not is_close(place, new_snow.station):
        station_sentence = describe_snow_station(place, new_snow.station, "new snow")
        swiss_measured_at = new_snow.measured_at.astimezone(SWISS_TZ)
        data_from_field = {
            "data_from": (
                f"{station_sentence} It covers the 24 hours and the 3 days up to {swiss_measured_at:%H:%M} on "
                f"{swiss_measured_at:%Y-%m-%d}."
            ),
        }
    return {
        **data_from_field,
        "measured_at": format_swiss_time(new_snow.measured_at),
        "new_snow_24_hours_cm": round_to_tenth(new_snow.new_snow_24_hours_cm),
        "new_snow_3_days_cm": round_to_tenth(new_snow.new_snow_3_days_cm),
    }


def build_snow_answer(
    meteoswiss_source: MeteoSwissMeasurementSource, slf_source: SLFMeasurementSource, place: SwisstopoPlace
) -> SnowAnswer:
    """
    Build the measurements_snow answer for a place; run it in a worker thread, as it downloads.

    snow_depth or new_snow is None when no station close enough has a current value. Raise
    CannotAnswerError when no snow station is close enough, and when neither has a current value.
    """
    meteoswiss_snow_stations = meteoswiss_source.list_snow_stations()
    slf_snow_stations = list_slf_snow_stations(slf_source)
    # Both sources measure the snow depth, only SLF the new snow
    snow_depth_stations: list[SnowStation] = [*meteoswiss_snow_stations, *slf_snow_stations]
    snow_depth = _find_snow_depth(meteoswiss_source, slf_source, place, snow_depth_stations)
    new_snow = _find_new_snow(slf_source, place, slf_snow_stations)
    if snow_depth is None and new_snow is None:
        # The SLF stations for the new snow are among these, so none of them is close enough either
        close_snow_stations = rank_by_weighted_distance(
            place, snow_depth_stations, MAX_SNOW_STATION_WEIGHTED_DISTANCE_M
        )
        if not close_snow_stations:
            closest_snow_station_sentence = describe_closest_snow_station(place, snow_depth_stations)
            raise CannotAnswerError(closest_snow_station_sentence)
        raise CannotAnswerError(
            f"There are no current snow measurements near {place.display_name}: no snow station close enough in "
            f"distance and height has a current value. The SLF observers measure the snow from November to April."
        )

    # SLF measures every new snow, and the snow depth when its station is an SLF one
    uses_meteoswiss = snow_depth is not None and isinstance(snow_depth.station, MeteoSwissStation)
    uses_slf = new_snow is not None or (snow_depth is not None and isinstance(snow_depth.station, SLFStation))
    source = describe_source(uses_meteoswiss=uses_meteoswiss, uses_slf=uses_slf, uses_swisstopo=True)
    answer: SnowAnswer = {"place": place.name, "snow_depth": None, "new_snow": None, "source": source}
    if snow_depth is not None:
        answer["snow_depth"] = _build_snow_depth_block(place, snow_depth)
    if new_snow is not None:
        answer["new_snow"] = _build_new_snow_block(place, new_snow)
    return answer
