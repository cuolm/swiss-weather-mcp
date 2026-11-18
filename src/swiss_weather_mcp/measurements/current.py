"""Build the measurements_current answer: the latest measurements of the station for a place."""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, NotRequired, Optional, TypedDict

import requests

from ..distance import MAX_STATIONS_TRIED, calculate_weighted_distance_m, is_close, rank_by_weighted_distance
from ..errors import CannotAnswerError
from ..formatting import (
    SWISS_TZ,
    DataFromField,
    PlaceFields,
    describe_distance_and_height,
    find_compass_point,
    format_swiss_time,
    round_to_tenth,
)
from ..places import SwisstopoPlace
from . import parameters
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


class SLFStationBlock(DataFromField):
    """The slf_station block: the latest measurements of an SLF station that suits the place better."""
    measured_at: str
    temperature_c: Optional[float]
    humidity_percent: Optional[float]
    wind_speed_kmh: Optional[float]
    wind_direction_degrees: Optional[float]
    compass_point: Optional[str]
    strongest_gust_last_30_minutes_kmh: Optional[float]
    snow_surface_temperature_c: Optional[float]


class SLFStationField(TypedDict):
    """The slf_station field of the answer, when an SLF station suits the place better than the MeteoSwiss station."""
    slf_station: NotRequired[SLFStationBlock]


class CurrentAnswer(PlaceFields, SLFStationField):
    """The measurements_current answer."""
    measured_at: str
    temperature_c: Optional[float]
    humidity_percent: Optional[float]
    dew_point_c: Optional[float]
    rain_last_10_minutes_mm: Optional[float]
    sunshine_last_10_minutes_minutes: Optional[float]
    wind_speed_kmh: Optional[float]
    gusts_kmh: Optional[float]
    wind_direction_degrees: Optional[float]
    pressure_sea_level_hpa: Optional[float]
    compass_point: Optional[str]
    temperature_change_last_3_hours_c: Optional[float]
    pressure_change_last_3_hours_hpa: Optional[float]
    sunshine_last_hour_minutes: Optional[float]
    rain_last_hour_mm: Optional[float]
    strongest_gust_last_hour_kmh: Optional[float]


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


def _build_slf_station_block(
    place: SwisstopoPlace, slf_station_measurements: SLFStationMeasurements
) -> SLFStationBlock:
    """Build the slf_station block of the weather now: the latest measurements of an SLF station."""
    wind_direction_degrees = round_to_tenth(slf_station_measurements.wind_direction_degrees)
    if wind_direction_degrees is None:
        compass_point = None
    else:
        compass_point = find_compass_point(wind_direction_degrees)
    data_from_field: DataFromField = {}
    # A station next to the place stands for it, so the block does not name it
    if not is_close(place, slf_station_measurements.station):
        data_from_field = {"data_from": _describe_slf_station(place, slf_station_measurements)}
    snow_surface_temperature_c = _find_snow_surface_temperature_c(slf_station_measurements)
    return {
        **data_from_field,
        "measured_at": format_swiss_time(slf_station_measurements.measured_at),
        "temperature_c": round_to_tenth(slf_station_measurements.temperature_c),
        "humidity_percent": round_to_tenth(slf_station_measurements.humidity_percent),
        "wind_speed_kmh": round_to_tenth(slf_station_measurements.wind_speed_kmh),
        "wind_direction_degrees": wind_direction_degrees,
        "compass_point": compass_point,
        "strongest_gust_last_30_minutes_kmh": round_to_tenth(slf_station_measurements.strongest_gust_kmh),
        "snow_surface_temperature_c": round_to_tenth(snow_surface_temperature_c),
    }


def build_current_answer(
    meteoswiss_source: MeteoSwissMeasurementSource, slf_source: SLFMeasurementSource, place: SwisstopoPlace
) -> CurrentAnswer:
    """
    Build the measurements_current answer for a place; run it in a worker thread, as it downloads.

    Add the slf_station block when an SLF station suits the place better than the MeteoSwiss station.
    Raise CannotAnswerError when none of the MAX_STATIONS_TRIED closest stations published a temperature in
    the last hour.
    """
    all_measurements = _find_meteoswiss_station_measurements(meteoswiss_source, place)
    latest_measurements = all_measurements[-1]

    place_fields: PlaceFields = {"place": place.name}
    # A station next to the place stands for it, so the answer does not name it
    if not is_close(place, latest_measurements.station):
        place_fields = {"data_from": _describe_meteoswiss_station(place, latest_measurements), "place": place.name}

    parameter_to_value = latest_measurements.parameter_to_value
    wind_direction_degrees = parameter_to_value[parameters.WIND_DIRECTION]
    if wind_direction_degrees is None:
        compass_point = None
    else:
        compass_point = find_compass_point(wind_direction_degrees)
    temperature_change_c = _calculate_change_last_3_hours(all_measurements, parameters.TEMPERATURE)
    pressure_change_hpa = _calculate_change_last_3_hours(all_measurements, parameters.PRESSURE_SEA_LEVEL)
    sunshine_last_hour_minutes = _sum_last_hour(all_measurements, parameters.SUNSHINE)
    rain_last_hour_mm = _sum_last_hour(all_measurements, parameters.PRECIPITATION)
    strongest_gust_last_hour_kmh = _find_highest_last_hour(all_measurements, parameters.WIND_GUSTS)

    meteoswiss_station_weighted_distance_m = calculate_weighted_distance_m(place, latest_measurements.station)
    slf_station_measurements = _find_closer_slf_station_measurements(
        slf_source, place, meteoswiss_station_weighted_distance_m
    )
    slf_station_field: SLFStationField = {}
    if slf_station_measurements is not None:
        slf_station_field = {"slf_station": _build_slf_station_block(place, slf_station_measurements)}

    return {
        **place_fields,
        "measured_at": format_swiss_time(latest_measurements.measured_at),
        "temperature_c": parameter_to_value[parameters.TEMPERATURE],
        "humidity_percent": parameter_to_value[parameters.HUMIDITY],
        "dew_point_c": parameter_to_value[parameters.DEW_POINT],
        "rain_last_10_minutes_mm": parameter_to_value[parameters.PRECIPITATION],
        "sunshine_last_10_minutes_minutes": parameter_to_value[parameters.SUNSHINE],
        "wind_speed_kmh": parameter_to_value[parameters.WIND_SPEED],
        "gusts_kmh": parameter_to_value[parameters.WIND_GUSTS],
        "wind_direction_degrees": wind_direction_degrees,
        "pressure_sea_level_hpa": parameter_to_value[parameters.PRESSURE_SEA_LEVEL],
        "compass_point": compass_point,
        "temperature_change_last_3_hours_c": temperature_change_c,
        "pressure_change_last_3_hours_hpa": pressure_change_hpa,
        "sunshine_last_hour_minutes": sunshine_last_hour_minutes,
        "rain_last_hour_mm": rain_last_hour_mm,
        "strongest_gust_last_hour_kmh": strongest_gust_last_hour_kmh,
        **slf_station_field,
    }
