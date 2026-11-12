"""Build the measurements_current answer: the latest measurements of the station for a place."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from . import parameters
from ..distance import MAX_STATIONS_TRIED, calculate_weighted_distance_m, is_close, rank_by_weighted_distance
from ..errors import CannotAnswerError
from ..formatting import SWISS_TZ, describe_distance_and_height, find_compass_point, format_swiss_time, round_to_tenth
from ..places import SwisstopoPlace
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStationMeasurements
from .slf_source import SLFMeasurementSource, SLFStation, SLFStationMeasurements

logger = logging.getLogger(__name__)

# Older latest measurements mean the station stopped publishing
LATEST_MEASUREMENTS_MAX_AGE = timedelta(hours=1)
# The usual period for a pressure tendency
CHANGE_PERIOD = timedelta(hours=3)
LAST_HOUR = timedelta(hours=1)
# A station publishes a row every 10 minutes
MEASUREMENTS_PER_HOUR = 6
# On 2026-11-11, more than half of the SLF stations with less snow measured a snow surface above 0 °C
SNOW_SURFACE_MIN_SNOW_DEPTH_CM = 10

# The field each measured value fills in the answer
MEASURED_FIELDS = (
    ("temperature_c", parameters.TEMPERATURE),
    ("humidity_percent", parameters.HUMIDITY),
    ("dew_point_c", parameters.DEW_POINT),
    ("rain_last_10_minutes_mm", parameters.PRECIPITATION),
    ("sunshine_last_10_minutes_min", parameters.SUNSHINE),
    ("wind_speed_kmh", parameters.WIND_SPEED),
    ("gusts_kmh", parameters.WIND_GUSTS),
    ("wind_direction_degrees", parameters.WIND_DIRECTION),
    ("pressure_sea_level_hpa", parameters.PRESSURE_SEA_LEVEL),
)


def _describe_meteoswiss_station(
    place: SwisstopoPlace, meteoswiss_station_measurements: MeteoSwissStationMeasurements
) -> str:
    """Say which station measured the values for a place, how far away and how much higher or lower, and when."""
    station = meteoswiss_station_measurements.station
    distance_and_height = describe_distance_and_height(place, station)
    swiss_measured_at = meteoswiss_station_measurements.measured_at.astimezone(SWISS_TZ)
    return (
        f"The measurements for {place.display_name} are those of the MeteoSwiss station {station.display_name}, "
        f"{distance_and_height}. It measured the following values at {swiss_measured_at:%H:%M}."
    )


def _describe_slf_station(place: SwisstopoPlace, slf_station_measurements: SLFStationMeasurements) -> str:
    """Say which SLF station suits a place better than the MeteoSwiss station, and when its values end."""
    station = slf_station_measurements.station
    distance_and_height = describe_distance_and_height(place, station)
    swiss_measured_at = slf_station_measurements.measured_at.astimezone(SWISS_TZ)
    return (
        f"The SLF station {station.display_name}, {distance_and_height}, suits {place.display_name} better in "
        f"distance and height than the MeteoSwiss station. Its values cover the 30 minutes up to "
        f"{swiss_measured_at:%H:%M}."
    )


def _find_snow_surface_temperature_c(slf_station_measurements: SLFStationMeasurements) -> Optional[float]:
    """Return the snow surface temperature, or None with less than 10 cm of snow or no snow depth."""
    snow_depth_cm = slf_station_measurements.snow_depth_cm
    if snow_depth_cm is None or snow_depth_cm < SNOW_SURFACE_MIN_SNOW_DEPTH_CM:
        return None
    return slf_station_measurements.snow_surface_temperature_c


def _build_slf_station_block(place: SwisstopoPlace, slf_station_measurements: SLFStationMeasurements) -> Dict[str, Any]:
    """Build the slf_station block of the weather now: the latest measurements of an SLF station."""
    wind_direction_degrees = round_to_tenth(slf_station_measurements.wind_direction_degrees)
    if wind_direction_degrees is None:
        compass_point = None
    else:
        compass_point = find_compass_point(wind_direction_degrees)
    block: Dict[str, Any] = {}
    # A station next to the place stands for it, so the block does not name it
    if not is_close(place, slf_station_measurements.station):
        block["data_from"] = _describe_slf_station(place, slf_station_measurements)
    block["measured_at"] = format_swiss_time(slf_station_measurements.measured_at)
    block["temperature_c"] = round_to_tenth(slf_station_measurements.temperature_c)
    block["humidity_percent"] = round_to_tenth(slf_station_measurements.humidity_percent)
    block["wind_speed_kmh"] = round_to_tenth(slf_station_measurements.wind_speed_kmh)
    block["wind_direction_degrees"] = wind_direction_degrees
    block["compass_point"] = compass_point
    block["strongest_gust_last_30_minutes_kmh"] = round_to_tenth(slf_station_measurements.strongest_gust_kmh)
    snow_surface_temperature_c = _find_snow_surface_temperature_c(slf_station_measurements)
    block["snow_surface_temperature_c"] = round_to_tenth(snow_surface_temperature_c)
    return block


def _find_measurements_at(all_measurements: List[MeteoSwissStationMeasurements],
                          measured_at: datetime) -> Optional[MeteoSwissStationMeasurements]:
    """Find the measurements of a time, or None when the station published none for it."""
    for measurements in all_measurements:
        if measurements.measured_at == measured_at:
            return measurements
    return None


def _calculate_change_last_3_hours(
    all_measurements: List[MeteoSwissStationMeasurements], parameter: str
) -> Optional[float]:
    """Return how much a value changed over the last 3 hours, or None when either value is missing."""
    latest_measurements = all_measurements[-1]
    earlier_measurements = _find_measurements_at(all_measurements, latest_measurements.measured_at - CHANGE_PERIOD)
    if earlier_measurements is None:
        return None
    latest_value = latest_measurements.parameter_to_value[parameter]
    earlier_value = earlier_measurements.parameter_to_value[parameter]
    if latest_value is None or earlier_value is None:
        return None
    return round(latest_value - earlier_value, 1)


def _find_last_hour_values(all_measurements: List[MeteoSwissStationMeasurements], parameter: str) -> Optional[List[float]]:
    """Return the six 10-minute values of the last hour, or None when one of them is missing."""
    latest_measurements = all_measurements[-1]
    period_start = latest_measurements.measured_at - LAST_HOUR

    values: List[float] = []
    for measurements in all_measurements:
        if measurements.measured_at > period_start:
            value = measurements.parameter_to_value[parameter]
            if value is None:
                return None
            values.append(value)

    if len(values) < MEASUREMENTS_PER_HOUR:
        return None
    return values


def _sum_last_hour(all_measurements: List[MeteoSwissStationMeasurements], parameter: str) -> Optional[float]:
    """Return the total of a 10-minute value over the last hour, such as the rain, or None when one is missing."""
    values = _find_last_hour_values(all_measurements, parameter)
    if values is None:
        return None
    # Rounded, as adding tenths of a millimetre leaves float noise such as 0.30000000000000004
    return round(sum(values), 1)


def _find_highest_last_hour(all_measurements: List[MeteoSwissStationMeasurements], parameter: str) -> Optional[float]:
    """Return the highest 10-minute value of the last hour, such as the strongest gust, or None when one is missing."""
    values = _find_last_hour_values(all_measurements, parameter)
    if values is None:
        return None
    return max(values)


def _find_meteoswiss_station_measurements(
    meteoswiss_source: MeteoSwissMeasurementSource, place: SwisstopoPlace
) -> List[MeteoSwissStationMeasurements]:
    """
    Find the measurements of the MeteoSwiss station for a place, the oldest first.

    The stations are ranked by weighted distance, and the first of the first MAX_STATIONS_TRIED that
    published a temperature in the last hour is taken. Raise CannotAnswerError when none did.
    """
    meteoswiss_stations = meteoswiss_source.list_stations()
    ranked_meteoswiss_stations = rank_by_weighted_distance(place, meteoswiss_stations)
    now = datetime.now(timezone.utc)
    for station in ranked_meteoswiss_stations[:MAX_STATIONS_TRIED]:
        all_measurements = meteoswiss_source.read_station_measurements(station)
        # Some stations measure only a few values, such as wind on a tower, and would answer
        # with almost every value None; a temperature marks a station that measures the usual set
        if not all_measurements or all_measurements[-1].parameter_to_value[parameters.TEMPERATURE] is None:
            logger.info(f"Station {station.abbr} has no current temperature, trying the next station")
            continue
        latest_measurements = all_measurements[-1]
        if now - latest_measurements.measured_at > LATEST_MEASUREMENTS_MAX_AGE:
            logger.info(f"Station {station.abbr} has no measurements in the last hour, trying the next station")
            continue
        return all_measurements

    raise CannotAnswerError(
        f"None of the {MAX_STATIONS_TRIED} stations tried for {place.display_name} published a "
        f"temperature in the last hour, try again later."
    )


def _find_recent_slf_station_measurements(
    slf_source: SLFMeasurementSource, ranked_slf_stations: List[SLFStation]
) -> Optional[SLFStationMeasurements]:
    """Find the latest measurements of the first of the first MAX_STATIONS_TRIED stations with a recent temperature."""
    now = datetime.now(timezone.utc)
    for station in ranked_slf_stations[:MAX_STATIONS_TRIED]:
        latest_measurements = slf_source.read_latest_station_measurements(station)
        if latest_measurements is None or latest_measurements.temperature_c is None:
            logger.info(f"SLF station {station.code} has no current temperature, trying the next station")
            continue
        if now - latest_measurements.measured_at > LATEST_MEASUREMENTS_MAX_AGE:
            logger.info(f"SLF station {station.code} has no measurements in the last hour, trying the next station")
            continue
        return latest_measurements
    return None


def _find_closer_slf_station_measurements(
    slf_source: SLFMeasurementSource, place: SwisstopoPlace, meteoswiss_station_weighted_distance_m: float
) -> Optional[SLFStationMeasurements]:
    """
    Find the measurements of an SLF station that suits a place better than the MeteoSwiss station.

    Only SLF stations with a weighted distance up to the MeteoSwiss station's are tried. Return None
    when there is none, and when SLF cannot be reached, as the MeteoSwiss values answer alone.
    """
    try:
        slf_stations = slf_source.list_stations()
        ranked_slf_stations = rank_by_weighted_distance(place, slf_stations, meteoswiss_station_weighted_distance_m)
        return _find_recent_slf_station_measurements(slf_source, ranked_slf_stations)
    except requests.RequestException as error:
        logger.warning(f"Could not download the SLF measurements for {place.display_name}, answering without: {error}")
        return None


def build_current_answer(
    meteoswiss_source: MeteoSwissMeasurementSource, slf_source: SLFMeasurementSource, place: SwisstopoPlace
) -> Dict[str, Any]:
    """
    Build the measurements_current answer for a place; run it in a worker thread, as it downloads.

    Add the slf_station block when an SLF station suits the place better than the MeteoSwiss station.
    Raise CannotAnswerError when no station publishes a temperature.
    """
    all_measurements = _find_meteoswiss_station_measurements(meteoswiss_source, place)
    latest_measurements = all_measurements[-1]

    answer: Dict[str, Any] = {}
    # A station next to the place stands for it, so the answer does not name it
    if not is_close(place, latest_measurements.station):
        answer["data_from"] = _describe_meteoswiss_station(place, latest_measurements)
    answer["place"] = place.name
    answer["measured_at"] = format_swiss_time(latest_measurements.measured_at)
    for field, parameter in MEASURED_FIELDS:
        answer[field] = latest_measurements.parameter_to_value[parameter]

    wind_direction_degrees = latest_measurements.parameter_to_value[parameters.WIND_DIRECTION]
    if wind_direction_degrees is None:
        answer["compass_point"] = None
    else:
        answer["compass_point"] = find_compass_point(wind_direction_degrees)

    answer["temperature_change_last_3_hours_c"] = _calculate_change_last_3_hours(
        all_measurements, parameters.TEMPERATURE
    )
    answer["pressure_change_last_3_hours_hpa"] = _calculate_change_last_3_hours(
        all_measurements, parameters.PRESSURE_SEA_LEVEL
    )
    answer["sunshine_last_hour_min"] = _sum_last_hour(all_measurements, parameters.SUNSHINE)
    answer["rain_last_hour_mm"] = _sum_last_hour(all_measurements, parameters.PRECIPITATION)
    answer["strongest_gust_last_hour_kmh"] = _find_highest_last_hour(all_measurements, parameters.WIND_GUSTS)

    meteoswiss_station_weighted_distance_m = calculate_weighted_distance_m(place, latest_measurements.station)
    slf_station_measurements = _find_closer_slf_station_measurements(
        slf_source, place, meteoswiss_station_weighted_distance_m
    )
    if slf_station_measurements is not None:
        answer["slf_station"] = _build_slf_station_block(place, slf_station_measurements)
    return answer
