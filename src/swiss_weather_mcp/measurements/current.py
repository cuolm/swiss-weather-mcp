"""Build the measurements_current answer: the latest measurements of the station for a place."""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from . import parameters
from ..distance import calculate_weighted_distance_m
from ..formatting import SWISS_TZ, describe_distance_and_height, find_compass_point, format_swiss_time, round_to_tenth
from ..places import SwisstopoPlace
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStationMeasurements
from .slf_source import SLFMeasurementSource, SLFStationMeasurements

logger = logging.getLogger(__name__)

KMH_PER_M_PER_S = 3.6
# The usual period for a pressure tendency
CHANGE_PERIOD = timedelta(hours=3)
LAST_HOUR = timedelta(hours=1)
# A station publishes a row every 10 minutes
MEASUREMENTS_PER_HOUR = 6

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


def _build_data_from_sentence(place: SwisstopoPlace, measurements: MeteoSwissStationMeasurements) -> str:
    """Build the data_from sentence: the station that measured the values, and when it measured them."""
    station = measurements.station
    distance_and_height = describe_distance_and_height(place, station)
    measured_at = measurements.measured_at.astimezone(SWISS_TZ)
    return (
        f"The measurements for {place.display_name} are those of the MeteoSwiss station {station.display_name}, "
        f"{distance_and_height}. It measured the following values at {measured_at:%H:%M}."
    )


def _build_slf_data_from_sentence(place: SwisstopoPlace, slf_measurements: SLFStationMeasurements) -> str:
    """Build the data_from sentence of the SLF block: the station, and the time its values cover."""
    station = slf_measurements.station
    distance_and_height = describe_distance_and_height(place, station)
    measured_at = slf_measurements.measured_at.astimezone(SWISS_TZ)
    return (
        f"The SLF station {station.display_name}, {distance_and_height}, suits {place.display_name} better in "
        f"distance and height than the MeteoSwiss station. Its values cover the 30 minutes up to {measured_at:%H:%M}."
    )


def _convert_m_per_s_to_kmh(speed_m_per_s: Optional[float]) -> Optional[float]:
    """Turn a speed in m/s into km/h rounded to a tenth, or return None for None."""
    if speed_m_per_s is None:
        return None
    return round(speed_m_per_s * KMH_PER_M_PER_S, 1)


def _build_slf_block(place: SwisstopoPlace, slf_measurements: SLFStationMeasurements) -> Dict[str, Any]:
    """Build the slf_station block of the weather now: the latest measurements of an SLF station."""
    wind_direction_degrees = round_to_tenth(slf_measurements.wind_direction_degrees)
    if wind_direction_degrees is None:
        compass_point = None
    else:
        compass_point = find_compass_point(wind_direction_degrees)
    return {
        "data_from": _build_slf_data_from_sentence(place, slf_measurements),
        "measured_at": format_swiss_time(slf_measurements.measured_at),
        "temperature_c": round_to_tenth(slf_measurements.temperature_c),
        "humidity_percent": round_to_tenth(slf_measurements.humidity_percent),
        "wind_speed_kmh": _convert_m_per_s_to_kmh(slf_measurements.wind_speed_m_per_s),
        "wind_direction_degrees": wind_direction_degrees,
        "compass_point": compass_point,
        "strongest_gust_last_30_minutes_kmh": _convert_m_per_s_to_kmh(slf_measurements.strongest_gust_m_per_s),
    }


def _find_measurements_at(all_measurements: List[MeteoSwissStationMeasurements],
                          measured_at: datetime) -> Optional[MeteoSwissStationMeasurements]:
    """Find the measurements of a time, or None when the station published none for it."""
    for measurements in all_measurements:
        if measurements.measured_at == measured_at:
            return measurements
    return None


def _calculate_change(all_measurements: List[MeteoSwissStationMeasurements], parameter: str) -> Optional[float]:
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


def _find_closer_slf_station_measurements(
    slf_source: SLFMeasurementSource, place: SwisstopoPlace, meteoswiss_station_weighted_distance_m: float
) -> Optional[SLFStationMeasurements]:
    """
    Find the measurements of an SLF station that suits a place better than the MeteoSwiss station.

    Return None when there is none, and when SLF cannot be reached, as the MeteoSwiss values answer alone.
    """
    try:
        return slf_source.find_closer_slf_station_measurements(place, meteoswiss_station_weighted_distance_m)
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
    all_measurements = meteoswiss_source.find_station_measurements(place)
    latest_measurements = all_measurements[-1]

    answer: Dict[str, Any] = {
        "data_from": _build_data_from_sentence(place, latest_measurements),
        "place": place.name,
        "measured_at": format_swiss_time(latest_measurements.measured_at),
    }
    for field, parameter in MEASURED_FIELDS:
        answer[field] = latest_measurements.parameter_to_value[parameter]

    wind_direction_degrees = latest_measurements.parameter_to_value[parameters.WIND_DIRECTION]
    if wind_direction_degrees is None:
        answer["compass_point"] = None
    else:
        answer["compass_point"] = find_compass_point(wind_direction_degrees)

    answer["temperature_change_last_3_hours_c"] = _calculate_change(all_measurements, parameters.TEMPERATURE)
    answer["pressure_change_last_3_hours_hpa"] = _calculate_change(all_measurements, parameters.PRESSURE_SEA_LEVEL)
    answer["sunshine_last_hour_min"] = _sum_last_hour(all_measurements, parameters.SUNSHINE)
    answer["rain_last_hour_mm"] = _sum_last_hour(all_measurements, parameters.PRECIPITATION)
    answer["strongest_gust_last_hour_kmh"] = _find_highest_last_hour(all_measurements, parameters.WIND_GUSTS)

    meteoswiss_station_weighted_distance_m = calculate_weighted_distance_m(place, latest_measurements.station)
    slf_measurements = _find_closer_slf_station_measurements(slf_source, place, meteoswiss_station_weighted_distance_m)
    if slf_measurements is not None:
        answer["slf_station"] = _build_slf_block(place, slf_measurements)
    return answer
