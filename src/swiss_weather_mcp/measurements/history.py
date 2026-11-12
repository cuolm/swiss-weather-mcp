"""Build the measurements_history answer: the daily weather and snow measurements of the stations for a place."""
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

import requests

from . import parameters
from ..distance import MAX_STATIONS_TRIED, is_close_to_place, rank_by_weighted_distance
from ..errors import CannotAnswerError
from ..formatting import describe_distance_and_height
from ..places import SwisstopoPlace
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStation, MeteoSwissStationMeasurements
from .snow import MAX_SNOW_STATION_WEIGHTED_DISTANCE_M, describe_closest_snow_station, describe_snow_station

logger = logging.getLogger(__name__)

MINUTES_PER_HOUR = 60
# Up to a whole year, a leap year too
MAX_HISTORY_DAYS = 366

# Which measurements the answer holds; without a choice it holds both
HistoryValues = Literal["weather", "snow"]
WEATHER_VALUES = "weather"
SNOW_VALUES = "snow"


def _describe_meteoswiss_station(place: SwisstopoPlace, station: MeteoSwissStation) -> str:
    """Say which station measured the values for a place, how far away and how much higher or lower."""
    distance_and_height = describe_distance_and_height(place, station)
    return (
        f"The measurements for {place.display_name} are those of the MeteoSwiss station {station.display_name}, "
        f"{distance_and_height}."
    )


def _convert_minutes_to_hours(minutes: Optional[float]) -> Optional[float]:
    """Turn minutes into hours rounded to a tenth, such as the sunshine of a day, or return None for None."""
    if minutes is None:
        return None
    return round(minutes / MINUTES_PER_HOUR, 1)


def _build_weather_day_row(day_measurements: MeteoSwissStationMeasurements) -> Dict[str, Any]:
    """Build the weather row of one day of a station's daily measurements."""
    day = day_measurements.measured_at.date()
    parameter_to_value = day_measurements.parameter_to_value
    sunshine_min = parameter_to_value[parameters.SUNSHINE_DAY]
    return {
        "date": day.isoformat(),
        "weekday": f"{day:%A}",
        "temperature_mean_c": parameter_to_value[parameters.TEMPERATURE_DAY_MEAN],
        "temperature_min_c": parameter_to_value[parameters.TEMPERATURE_DAY_MIN],
        "temperature_max_c": parameter_to_value[parameters.TEMPERATURE_DAY_MAX],
        "rain_mm": parameter_to_value[parameters.PRECIPITATION_DAY],
        "sunshine_h": _convert_minutes_to_hours(sunshine_min),
        "strongest_gust_kmh": parameter_to_value[parameters.WIND_GUSTS_DAY],
    }


def _build_snow_day_row(day_measurements: MeteoSwissStationMeasurements) -> Dict[str, Any]:
    """Build the snow row of one day of a manual site's daily measurements."""
    day = day_measurements.measured_at.date()
    parameter_to_value = day_measurements.parameter_to_value
    return {
        "date": day.isoformat(),
        "snow_depth_cm": parameter_to_value[parameters.SNOW_DEPTH_DAY],
        "new_snow_cm": parameter_to_value[parameters.NEW_SNOW_DAY],
    }


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


def _check_days_before_today(first_day: date, last_day: date) -> None:
    """Raise CannotAnswerError for today or a later day, which has no daily measurements yet."""
    today = datetime.now(timezone.utc).date()
    if last_day >= today:
        unmeasured_day = max(first_day, today)
        raise CannotAnswerError(
            f"There are no daily measurements for {unmeasured_day.isoformat()} yet. MeteoSwiss publishes "
            f"the values of a day the next morning, so use measurements_current for now and forecast_daily "
            f"for today and the days ahead."
        )


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


def _has_snow_value(day_measurements: MeteoSwissStationMeasurements) -> bool:
    """Tell whether a manual site measured the snow depth or the new snow on a day."""
    parameter_to_value = day_measurements.parameter_to_value
    has_snow_depth = parameter_to_value[parameters.SNOW_DEPTH_DAY] is not None
    has_new_snow = parameter_to_value[parameters.NEW_SNOW_DAY] is not None
    return has_snow_depth or has_new_snow


def _find_meteoswiss_manual_site_daily_measurements(
    meteoswiss_source: MeteoSwissMeasurementSource, place: SwisstopoPlace, first_day: date, last_day: date
) -> List[MeteoSwissStationMeasurements]:
    """
    Find the daily snow of the manual site for a place from first_day to last_day, the oldest first.

    The manual sites that started measuring the snow depth by last_day and lie within
    MAX_SNOW_STATION_WEIGHTED_DISTANCE_M are ranked by weighted distance, and the first of the first
    MAX_STATIONS_TRIED with a snow value on one of the days is taken; a day it has no row for is left out.
    Raise CannotAnswerError when no site measured the snow then, none is close enough, MeteoSwiss has not
    published the days yet, or none of the sites tried has snow values for them.
    """
    described_days = _describe_days(first_day, last_day)
    manual_sites = meteoswiss_source.list_manual_sites(last_day)
    if not manual_sites:
        raise CannotAnswerError(f"No MeteoSwiss observer measured the snow depth on {described_days}.")
    ranked_manual_sites = rank_by_weighted_distance(place, manual_sites, MAX_SNOW_STATION_WEIGHTED_DISTANCE_M)
    if not ranked_manual_sites:
        closest_snow_station_sentence = describe_closest_snow_station(place, manual_sites)
        raise CannotAnswerError(closest_snow_station_sentence)

    tried_manual_sites = ranked_manual_sites[:MAX_STATIONS_TRIED]
    newest_published_days = []
    for manual_site in tried_manual_sites:
        all_days = meteoswiss_source.read_station_daily_measurements(manual_site, first_day, last_day)
        if all_days:
            newest_published_days.append(all_days[-1].measured_at.date())

        asked_days = _find_days_between(all_days, first_day, last_day)
        has_snow = any(_has_snow_value(day_measurements) for day_measurements in asked_days)
        if has_snow:
            return asked_days
        logger.info(f"Manual site {manual_site.abbr} has no snow for {described_days}, trying the next one")

    # As for the weather: days after the newest row of every site tried are not published yet
    if newest_published_days and max(newest_published_days) < first_day:
        raise CannotAnswerError(
            f"MeteoSwiss has not published the daily measurements for {described_days} yet, try again later."
        )
    tried_count = len(tried_manual_sites)
    raise CannotAnswerError(
        f"None of the {tried_count} snow stations tried for {place.display_name} has snow values for {described_days}."
    )


def _build_weather_block(
    meteoswiss_source: MeteoSwissMeasurementSource, place: SwisstopoPlace, first_day: date, last_day: date
) -> Dict[str, Any]:
    """Build the weather block from the daily measurements of the station for a place; raise CannotAnswerError as its find does."""
    daily_measurements = _find_meteoswiss_station_daily_measurements(meteoswiss_source, place, first_day, last_day)
    block: Dict[str, Any] = {}
    station = daily_measurements[0].station
    # A station next to the place stands for it, so the block does not name it
    if not is_close_to_place(place, station):
        block["data_from"] = _describe_meteoswiss_station(place, station)
    day_rows = []
    for day_measurements in daily_measurements:
        day_row = _build_weather_day_row(day_measurements)
        day_rows.append(day_row)
    block["days"] = day_rows
    return block


def _build_snow_block(
    meteoswiss_source: MeteoSwissMeasurementSource, place: SwisstopoPlace, first_day: date, last_day: date
) -> Dict[str, Any]:
    """Build the snow block from the daily snow of the manual site for a place; raise CannotAnswerError as its find does."""
    daily_measurements = _find_meteoswiss_manual_site_daily_measurements(meteoswiss_source, place, first_day, last_day)
    block: Dict[str, Any] = {}
    manual_site = daily_measurements[0].station
    # A manual site next to the place stands for it, so the block does not name it
    if not is_close_to_place(place, manual_site):
        block["data_from"] = describe_snow_station(place, manual_site, "snow")
    day_rows = []
    for day_measurements in daily_measurements:
        day_row = _build_snow_day_row(day_measurements)
        day_rows.append(day_row)
    block["days"] = day_rows
    return block


def check_day_count(days: int) -> None:
    """Raise CannotAnswerError when days is not between 1 and MAX_HISTORY_DAYS."""
    if not 1 <= days <= MAX_HISTORY_DAYS:
        raise CannotAnswerError(f"days must be between 1 and {MAX_HISTORY_DAYS}, not {days}.")


def build_history_answer(
    meteoswiss_source: MeteoSwissMeasurementSource,
    place: SwisstopoPlace,
    first_day: date,
    days: int,
    values: Optional[HistoryValues] = None,
) -> Dict[str, Any]:
    """
    Build the measurements_history answer for a number of UTC days from first_day; run it in a worker thread.

    Raise CannotAnswerError for today or later, and when no station has the values asked for. Without
    values, a missing snow block is None.
    """
    last_day = first_day + timedelta(days=days - 1)
    _check_days_before_today(first_day, last_day)

    answer: Dict[str, Any] = {"place": place.name}
    if values == WEATHER_VALUES:
        answer["weather"] = _build_weather_block(meteoswiss_source, place, first_day, last_day)
    elif values == SNOW_VALUES:
        answer["snow"] = _build_snow_block(meteoswiss_source, place, first_day, last_day)
    else:
        answer["weather"] = _build_weather_block(meteoswiss_source, place, first_day, last_day)
        # Next to the weather the snow is optional: without it the answer still holds the weather
        try:
            answer["snow"] = _build_snow_block(meteoswiss_source, place, first_day, last_day)
        except CannotAnswerError:
            answer["snow"] = None
        except requests.RequestException as error:
            logger.warning(f"Could not download the manual sites, answering without the snow: {error}")
            answer["snow"] = None
    return answer
