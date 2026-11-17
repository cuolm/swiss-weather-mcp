"""Build the measurements_history answer: the daily weather and snow measurements of the stations for a place."""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Literal, NotRequired, Optional, TypedDict

import requests

from . import parameters
from ..distance import MAX_STATIONS_TRIED, is_close, rank_by_weighted_distance
from ..errors import CannotAnswerError
from ..formatting import DataFromField, describe_distance_and_height
from ..places import SwisstopoPlace
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStation, MeteoSwissStationMeasurements
from .slf_source import SLFMeasurementSource
from .snow import (
    MAX_SNOW_STATION_WEIGHTED_DISTANCE_M, describe_closest_snow_station, describe_snow_station, list_slf_snow_stations,
)
from .snow_values import SnowStationDailyMeasurements, SnowStation

logger = logging.getLogger(__name__)

MINUTES_PER_HOUR = 60
# Up to a whole year, a leap year too
MAX_HISTORY_DAYS = 366

# Which measurements the answer holds; without a choice it holds both
HistoryValues = Literal["weather", "snow"]
WEATHER_VALUES = "weather"
SNOW_VALUES = "snow"


class RainField(TypedDict):
    """The rain of a day: rain_mm of the UTC day, or rain_06_to_06_utc_mm when only that was measured."""
    rain_mm: NotRequired[Optional[float]]
    rain_06_to_06_utc_mm: NotRequired[float]


class WeatherDayRow(RainField):
    """The weather of one day at a station; None where it has no value."""
    date: str
    weekday: str
    temperature_mean_c: Optional[float]
    temperature_min_c: Optional[float]
    temperature_max_c: Optional[float]
    sunshine_h: Optional[float]
    strongest_gust_kmh: Optional[float]


class WeatherBlock(DataFromField):
    """The weather block of the measurements_history answer."""
    days: List[WeatherDayRow]


class SnowDayRow(TypedDict):
    """The snow of one day at a station; None where it has no value."""
    date: str
    snow_depth_cm: Optional[float]
    new_snow_cm: Optional[float]


class SnowBlock(DataFromField):
    """The snow block of the measurements_history answer."""
    days: List[SnowDayRow]


class HistoryAnswer(TypedDict):
    """The measurements_history answer; snow is None when it was not asked alone and no station has it."""
    place: str
    weather: NotRequired[WeatherBlock]
    snow: NotRequired[Optional[SnowBlock]]


def _find_last_past_day(first_day: date, last_day: date) -> date:
    """
    Find the last day before today up to last_day, as today and later days have no daily measurements yet.

    Raise CannotAnswerError when first_day is today or later.
    """
    today = datetime.now(timezone.utc).date()
    if first_day >= today:
        raise CannotAnswerError(
            f"There are no daily measurements for {first_day.isoformat()} yet. MeteoSwiss publishes "
            f"the values of a day the next morning, so use measurements_current for now and forecast_daily "
            f"for today and the days ahead."
        )
    yesterday = today - timedelta(days=1)
    return min(last_day, yesterday)


def _describe_days(first_day: date, last_day: date) -> str:
    """Say one day or a period of days for a message, such as "2026-11-04" or "2026-11-01 to 2026-11-04"."""
    if first_day == last_day:
        return first_day.isoformat()
    return f"{first_day.isoformat()} to {last_day.isoformat()}"


def _find_days_between(
    all_days: List[MeteoSwissStationMeasurements], first_day: date, last_day: date
) -> List[MeteoSwissStationMeasurements]:
    """Find the daily rows from first_day to last_day."""
    days_between = []
    for day_measurements in all_days:
        if first_day <= day_measurements.measured_at.date() <= last_day:
            days_between.append(day_measurements)
    return days_between


def _find_meteoswiss_station_daily_measurements(
    meteoswiss_source: MeteoSwissMeasurementSource, place: SwisstopoPlace, first_day: date, last_day: date
) -> List[MeteoSwissStationMeasurements]:
    """
    Find the daily measurements of the MeteoSwiss station for a place from first_day to last_day, the oldest first.

    The stations are ranked by weighted distance, and the first of the first MAX_STATIONS_TRIED with a
    temperature on one of the days is taken; a day it has no row for is left out. Raise
    CannotAnswerError for days MeteoSwiss has not published yet, and when none of the stations has a
    temperature on the days.
    """
    described_days = _describe_days(first_day, last_day)
    meteoswiss_stations = meteoswiss_source.list_stations()
    ranked_meteoswiss_stations = rank_by_weighted_distance(place, meteoswiss_stations)
    newest_published_days = []
    for station in ranked_meteoswiss_stations[:MAX_STATIONS_TRIED]:
        # The station table says on which day each station started measuring. A station that started after
        # the asked days has no values for them, so it is skipped without downloading its files.
        if station.data_since > last_day:
            logger.info(f"Station {station.abbr} started on {station.data_since}, trying the next station")
            continue

        all_days = meteoswiss_source.read_station_daily_measurements(station, first_day, last_day)
        if all_days:
            newest_published_days.append(all_days[-1].measured_at.date())

        asked_days = _find_days_between(all_days, first_day, last_day)
        has_temperature = any(
            day_measurements.parameter_to_value[parameters.TEMPERATURE_DAY_MEAN] is not None
            for day_measurements in asked_days
        )
        if has_temperature:
            return asked_days
        logger.info(f"Station {station.abbr} has no temperature for {described_days}, trying the next station")

    # MeteoSwiss publishes the days of all stations together, so days after the newest row of every
    # station tried are not published yet
    if newest_published_days and max(newest_published_days) < first_day:
        raise CannotAnswerError(
            f"MeteoSwiss has not published the daily measurements for {described_days} yet, try again later."
        )
    raise CannotAnswerError(
        f"None of the {MAX_STATIONS_TRIED} stations tried for {place.display_name} has a daily temperature "
        f"for {described_days}."
    )


def _describe_meteoswiss_station(place: SwisstopoPlace, station: MeteoSwissStation) -> str:
    """Say which station measured the values for a place, how far away and how much higher or lower."""
    distance_and_height = describe_distance_and_height(place, station)
    return (
        f"The measurements for {place.display_name} are those of the MeteoSwiss station {station.display_name}, "
        f"{distance_and_height}."
    )


def _build_rain_field(parameter_to_value: Dict[str, Optional[float]]) -> RainField:
    """Build the rain of a day: of the UTC day, or from 06:00 to 06:00 UTC when only that was measured."""
    rain_mm = parameter_to_value[parameters.PRECIPITATION_DAY]
    rain_06_to_06_utc_mm = parameter_to_value[parameters.PRECIPITATION_DAY_6_TO_6_UTC]
    if rain_mm is None and rain_06_to_06_utc_mm is not None:
        return {"rain_06_to_06_utc_mm": rain_06_to_06_utc_mm}
    return {"rain_mm": rain_mm}


def _convert_minutes_to_hours(minutes: Optional[float]) -> Optional[float]:
    """Turn minutes into hours rounded to a tenth, such as the sunshine of a day, or return None for None."""
    if minutes is None:
        return None
    return round(minutes / MINUTES_PER_HOUR, 1)


def _build_weather_day_row(day_measurements: MeteoSwissStationMeasurements) -> WeatherDayRow:
    """Build the weather row of one day of a station's daily measurements."""
    day = day_measurements.measured_at.date()
    parameter_to_value = day_measurements.parameter_to_value
    rain_field = _build_rain_field(parameter_to_value)
    sunshine_min = parameter_to_value[parameters.SUNSHINE_DAY]
    return {
        "date": day.isoformat(),
        "weekday": f"{day:%A}",
        "temperature_mean_c": parameter_to_value[parameters.TEMPERATURE_DAY_MEAN],
        "temperature_min_c": parameter_to_value[parameters.TEMPERATURE_DAY_MIN],
        "temperature_max_c": parameter_to_value[parameters.TEMPERATURE_DAY_MAX],
        **rain_field,
        "sunshine_h": _convert_minutes_to_hours(sunshine_min),
        "strongest_gust_kmh": parameter_to_value[parameters.WIND_GUSTS_DAY],
    }


def _build_weather_block(
    meteoswiss_source: MeteoSwissMeasurementSource, place: SwisstopoPlace, first_day: date, last_day: date
) -> WeatherBlock:
    """Build the weather block for a place from the best-suited station; raise CannotAnswerError if none has values."""
    daily_measurements = _find_meteoswiss_station_daily_measurements(meteoswiss_source, place, first_day, last_day)
    data_from_field: DataFromField = {}
    station = daily_measurements[0].station
    # A station next to the place stands for it, so the block does not name it
    if not is_close(place, station):
        data_from_field = {"data_from": _describe_meteoswiss_station(place, station)}
    day_rows = []
    for day_measurements in daily_measurements:
        day_row = _build_weather_day_row(day_measurements)
        day_rows.append(day_row)
    return {**data_from_field, "days": day_rows}


def _list_snow_stations_to_try(ranked_snow_stations: List[SnowStation]) -> List[SnowStation]:
    """List the first MAX_STATIONS_TRIED stations of each source, MeteoSwiss and SLF, in their order."""
    meteoswiss_count = 0
    slf_count = 0
    snow_stations_to_try: List[SnowStation] = []
    for station in ranked_snow_stations:
        if isinstance(station, MeteoSwissStation):
            meteoswiss_count += 1
            if meteoswiss_count <= MAX_STATIONS_TRIED:
                snow_stations_to_try.append(station)
        else:
            slf_count += 1
            if slf_count <= MAX_STATIONS_TRIED:
                snow_stations_to_try.append(station)
    return snow_stations_to_try


def _read_snow_history(
    meteoswiss_source: MeteoSwissMeasurementSource,
    slf_source: SLFMeasurementSource,
    station: SnowStation,
    first_day: date,
    last_day: date,
) -> List[SnowStationDailyMeasurements]:
    """Read a station's snow from its source, or none when its SLF history cannot be downloaded."""
    if isinstance(station, MeteoSwissStation):
        return meteoswiss_source.read_snow_history(station, first_day, last_day)
    try:
        return slf_source.read_snow_history(station, last_day)
    except requests.RequestException as error:
        logger.warning(f"Could not download the snow history of SLF station {station.code}: {error}")
        return []


def _has_snow_value(day_measurements: SnowStationDailyMeasurements) -> bool:
    """Tell whether a station measured the snow depth or the new snow on a day."""
    return day_measurements.snow_depth_cm is not None or day_measurements.new_snow_cm is not None


def _find_snow_station_daily_measurements(
    meteoswiss_source: MeteoSwissMeasurementSource,
    slf_source: SLFMeasurementSource,
    place: SwisstopoPlace,
    first_day: date,
    last_day: date,
) -> List[SnowStationDailyMeasurements]:
    """
    Find the daily measurements of the snow station for a place from first_day to last_day, the oldest first.

    The MeteoSwiss manual sites that started measuring the snow depth by last_day and the SLF snow stations
    within MAX_SNOW_STATION_WEIGHTED_DISTANCE_M are ranked by weighted distance. The first MAX_STATIONS_TRIED
    of each source are tried in that order, and the first with a snow value on one of the days is taken; a
    day it has no row for is left out. Raise CannotAnswerError when no station is close enough, the days are
    not published yet, or none of the stations tried has snow values for them.
    """
    described_days = _describe_days(first_day, last_day)
    meteoswiss_manual_sites = meteoswiss_source.list_manual_sites(last_day)
    slf_snow_stations = list_slf_snow_stations(slf_source)
    snow_stations: List[SnowStation] = [*meteoswiss_manual_sites, *slf_snow_stations]
    if not snow_stations:
        raise CannotAnswerError(f"No snow station measured the snow depth on {described_days}.")
    ranked_snow_stations = rank_by_weighted_distance(place, snow_stations, MAX_SNOW_STATION_WEIGHTED_DISTANCE_M)
    if not ranked_snow_stations:
        closest_snow_station_sentence = describe_closest_snow_station(place, snow_stations)
        raise CannotAnswerError(closest_snow_station_sentence)

    # Each source has its own tries: many SLF stations started after the asked days, and most SLF observers
    # pause in summer, so they would use up the tries of a MeteoSwiss site that measured
    tried_snow_stations = _list_snow_stations_to_try(ranked_snow_stations)
    newest_published_days = []
    for station in tried_snow_stations:
        daily_measurements = _read_snow_history(meteoswiss_source, slf_source, station, first_day, last_day)
        if daily_measurements:
            newest_published_days.append(daily_measurements[-1].day)

        asked_daily_measurements = []
        for day_measurements in daily_measurements:
            if first_day <= day_measurements.day <= last_day:
                asked_daily_measurements.append(day_measurements)
        has_snow = any(_has_snow_value(day_measurements) for day_measurements in asked_daily_measurements)
        if has_snow:
            return asked_daily_measurements
        logger.info(f"Snow station {station.display_name} has no snow for {described_days}, trying the next one")

    # As for the weather: days after the newest row of every station tried are not published yet
    if newest_published_days and max(newest_published_days) < first_day:
        raise CannotAnswerError(f"The snow measurements for {described_days} are not published yet, try again later.")
    tried_count = len(tried_snow_stations)
    raise CannotAnswerError(
        f"None of the {tried_count} snow stations tried for {place.display_name} has snow values for {described_days}."
    )


def _build_snow_day_row(day_measurements: SnowStationDailyMeasurements) -> SnowDayRow:
    """Build the snow row of one day."""
    return {
        "date": day_measurements.day.isoformat(),
        "snow_depth_cm": day_measurements.snow_depth_cm,
        "new_snow_cm": day_measurements.new_snow_cm,
    }


def _build_snow_block(
    meteoswiss_source: MeteoSwissMeasurementSource,
    slf_source: SLFMeasurementSource,
    place: SwisstopoPlace,
    first_day: date,
    last_day: date,
) -> SnowBlock:
    """Build the snow block for a place from the best-suited station; raise CannotAnswerError if none has values."""
    daily_measurements = _find_snow_station_daily_measurements(
        meteoswiss_source, slf_source, place, first_day, last_day
    )
    data_from_field: DataFromField = {}
    station = daily_measurements[0].station
    # A station next to the place stands for it, so the block does not name it
    if not is_close(place, station):
        data_from_field = {"data_from": describe_snow_station(place, station, "snow")}
    day_rows = []
    for day_measurements in daily_measurements:
        day_row = _build_snow_day_row(day_measurements)
        day_rows.append(day_row)
    return {**data_from_field, "days": day_rows}


def check_day_count(days: int) -> None:
    """Raise CannotAnswerError when days is not between 1 and MAX_HISTORY_DAYS."""
    if not 1 <= days <= MAX_HISTORY_DAYS:
        raise CannotAnswerError(f"days must be between 1 and {MAX_HISTORY_DAYS}, not {days}.")


def build_history_answer(
    meteoswiss_source: MeteoSwissMeasurementSource,
    slf_source: SLFMeasurementSource,
    place: SwisstopoPlace,
    first_day: date,
    days: int,
    values: Optional[HistoryValues] = None,
) -> HistoryAnswer:
    """
    Build the measurements_history answer for a number of UTC days from first_day; run it in a worker thread.

    Days from today on are left out. Raise CannotAnswerError when first_day is today or later, and when
    no station has the values asked for. Without values, a missing snow block is None.
    """
    asked_last_day = first_day + timedelta(days=days - 1)
    last_day = _find_last_past_day(first_day, asked_last_day)

    answer: HistoryAnswer = {"place": place.name}
    if values == WEATHER_VALUES:
        answer["weather"] = _build_weather_block(meteoswiss_source, place, first_day, last_day)
    elif values == SNOW_VALUES:
        answer["snow"] = _build_snow_block(meteoswiss_source, slf_source, place, first_day, last_day)
    else:
        answer["weather"] = _build_weather_block(meteoswiss_source, place, first_day, last_day)
        # Next to the weather the snow is optional: without it the answer still holds the weather
        try:
            answer["snow"] = _build_snow_block(meteoswiss_source, slf_source, place, first_day, last_day)
        except CannotAnswerError:
            answer["snow"] = None
        except requests.RequestException as error:
            logger.warning(f"Could not download the snow stations, answering without the snow: {error}")
            answer["snow"] = None
    return answer
