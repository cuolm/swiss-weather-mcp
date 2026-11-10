"""Build the measurements_snow answer: the snow depth and the new snow for a place, each from its own station."""
import logging
from typing import Any, Dict, List, Optional, TypeVar

import requests

from ..distance import GridPosition, calculate_weighted_distance_m
from ..errors import CannotAnswerError
from ..formatting import SWISS_TZ, describe_distance_and_height, format_swiss_time, round_to_tenth
from ..places import SwisstopoPlace
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStation
from .slf_source import SLFMeasurementSource, SLFStation
from .snow_values import NewSnow, SnowDepth, SnowStation

logger = logging.getLogger(__name__)

# As many as the sources try for the weather now
MAX_SNOW_STATIONS_TRIED = 3
# Snow changes with height and terrain, so a station farther than this by distance plus 20 times the
# height gap, such as 15 km away and 500 m higher, says too little about a place. Tested on the 5817
# forecast points: 94 % of those between 1200 and 2000 m keep a new snow station, and 88 % of those below
# 800 m, where the nearest SLF station is often 40 km away and 600 m higher, get none.
MAX_SNOW_STATION_WEIGHTED_DISTANCE_M = 25000

# Any station list, so ranking SLF stations alone still returns SLF stations
SnowStationT = TypeVar("SnowStationT", bound=GridPosition)


def _describe_snow_station(station: SnowStation) -> str:
    """Say where a snow value comes from, such as "comes from the MeteoSwiss station Davos (1594 m)"."""
    if isinstance(station, MeteoSwissStation):
        return f"comes from the MeteoSwiss station {station.display_name}"
    if station.is_manual_site:
        return f"was measured by an observer at {station.display_name}"
    return f"comes from the automatic SLF station {station.display_name}"


def _build_snow_depth_block(place: SwisstopoPlace, snow_depth: SnowDepth) -> Dict[str, Any]:
    """Build the snow_depth block of the snow answer."""
    station_source = _describe_snow_station(snow_depth.station)
    distance_and_height = describe_distance_and_height(place, snow_depth.station)
    measured_at = snow_depth.measured_at.astimezone(SWISS_TZ)
    data_from = (
        f"The snow depth for {place.display_name} {station_source}, {distance_and_height}. "
        f"It was measured at {measured_at:%H:%M} on {measured_at:%Y-%m-%d}."
    )
    return {
        "data_from": data_from,
        "measured_at": format_swiss_time(snow_depth.measured_at),
        "snow_depth_cm": round_to_tenth(snow_depth.snow_depth_cm),
    }


def _build_new_snow_block(place: SwisstopoPlace, new_snow: NewSnow) -> Dict[str, Any]:
    """Build the new_snow block of the snow answer."""
    station_source = _describe_snow_station(new_snow.station)
    distance_and_height = describe_distance_and_height(place, new_snow.station)
    measured_at = new_snow.measured_at.astimezone(SWISS_TZ)
    data_from = (
        f"The new snow for {place.display_name} {station_source}, {distance_and_height}. "
        f"It covers the 24 hours and the 3 days up to {measured_at:%H:%M} on {measured_at:%Y-%m-%d}."
    )
    return {
        "data_from": data_from,
        "measured_at": format_swiss_time(new_snow.measured_at),
        "new_snow_24_hours_cm": round_to_tenth(new_snow.new_snow_24_hours_cm),
        "new_snow_3_days_cm": round_to_tenth(new_snow.new_snow_3_days_cm),
    }


def _rank_close_snow_stations(place: SwisstopoPlace, snow_stations: List[SnowStationT]) -> List[SnowStationT]:
    """Rank the stations within MAX_SNOW_STATION_WEIGHTED_DISTANCE_M of a place, the closest first."""
    close_snow_stations = []
    for station in snow_stations:
        if calculate_weighted_distance_m(place, station) <= MAX_SNOW_STATION_WEIGHTED_DISTANCE_M:
            close_snow_stations.append(station)
    return sorted(close_snow_stations, key=lambda station: calculate_weighted_distance_m(place, station))


def _list_slf_snow_stations(slf_source: SLFMeasurementSource) -> List[SLFStation]:
    """List the SLF snow stations, or none when SLF cannot be reached; the MeteoSwiss stations then answer alone."""
    try:
        return slf_source.list_snow_stations()
    except requests.RequestException as error:
        logger.warning(f"Could not download the SLF snow stations, answering without them: {error}")
        return []


def _read_snow_depth(
    meteoswiss_source: MeteoSwissMeasurementSource, slf_source: SLFMeasurementSource, station: SnowStation
) -> Optional[SnowDepth]:
    """Read a station's current snow depth from its source, or None when it has none or SLF cannot be reached."""
    if isinstance(station, MeteoSwissStation):
        return meteoswiss_source.read_snow_depth(station)
    try:
        return slf_source.read_snow_depth(station)
    except requests.RequestException as error:
        logger.warning(f"Could not download the snow depth of SLF station {station.code}: {error}")
        return None


def _read_new_snow(slf_source: SLFMeasurementSource, station: SLFStation) -> Optional[NewSnow]:
    """Read an SLF station's new snow, or None when it has none or SLF cannot be reached."""
    try:
        return slf_source.read_new_snow(station)
    except requests.RequestException as error:
        logger.warning(f"Could not download the new snow of SLF station {station.code}: {error}")
        return None


def _find_snow_depth(
    meteoswiss_source: MeteoSwissMeasurementSource,
    slf_source: SLFMeasurementSource,
    place: SwisstopoPlace,
    snow_stations: List[SnowStation],
) -> Optional[SnowDepth]:
    """
    Find the current snow depth for a place from the given snow stations.

    The stations within MAX_SNOW_STATION_WEIGHTED_DISTANCE_M are ranked by distance plus
    METRES_PER_HEIGHT_METRE times the height gap, and the first of the first MAX_SNOW_STATIONS_TRIED
    with a current snow depth is taken.
    """
    ranked_snow_stations = _rank_close_snow_stations(place, snow_stations)
    for station in ranked_snow_stations[:MAX_SNOW_STATIONS_TRIED]:
        snow_depth = _read_snow_depth(meteoswiss_source, slf_source, station)
        if snow_depth is not None:
            return snow_depth
        logger.info(f"Station {station.name} has no current snow depth, trying the next station")
    return None


def _find_new_snow(
    slf_source: SLFMeasurementSource, place: SwisstopoPlace, slf_snow_stations: List[SLFStation]
) -> Optional[NewSnow]:
    """
    Find the new snow for a place from the given SLF snow stations, the only ones that have it.

    The stations are chosen and ranked as for the snow depth, and the first of the first
    MAX_SNOW_STATIONS_TRIED with new snow from today or yesterday is taken.
    """
    ranked_snow_stations = _rank_close_snow_stations(place, slf_snow_stations)
    for station in ranked_snow_stations[:MAX_SNOW_STATIONS_TRIED]:
        new_snow = _read_new_snow(slf_source, station)
        if new_snow is not None:
            return new_snow
        logger.info(f"SLF station {station.code} has no current new snow, trying the next station")
    return None


def build_snow_answer(
    meteoswiss_source: MeteoSwissMeasurementSource, slf_source: SLFMeasurementSource, place: SwisstopoPlace
) -> Dict[str, Any]:
    """
    Build the measurements_snow answer for a place; run it in a worker thread, as it downloads.

    snow_depth or new_snow is None when no station close enough has a current value. Raise
    CannotAnswerError when neither has one.
    """
    meteoswiss_snow_stations = meteoswiss_source.list_snow_stations()
    slf_snow_stations = _list_slf_snow_stations(slf_source)
    # Both sources measure the snow depth, only SLF the new snow
    snow_depth_stations: List[SnowStation] = [*meteoswiss_snow_stations, *slf_snow_stations]
    snow_depth = _find_snow_depth(meteoswiss_source, slf_source, place, snow_depth_stations)
    new_snow = _find_new_snow(slf_source, place, slf_snow_stations)
    if snow_depth is None and new_snow is None:
        raise CannotAnswerError(
            f"There are no current snow measurements near {place.display_name}: no snow station close enough in "
            f"distance and height has a current value. The SLF observers measure the snow from November to April."
        )

    answer: Dict[str, Any] = {"place": place.name, "snow_depth": None, "new_snow": None}
    if snow_depth is not None:
        answer["snow_depth"] = _build_snow_depth_block(place, snow_depth)
    if new_snow is not None:
        answer["new_snow"] = _build_new_snow_block(place, new_snow)
    return answer
