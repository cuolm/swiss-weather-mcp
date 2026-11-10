"""Build the measurements_history answer: the daily measurements of the station for a place."""
from datetime import date, timedelta
from typing import Any, Dict, Optional

from . import parameters
from ..errors import CannotAnswerError
from ..formatting import describe_distance_and_height
from ..places import SwisstopoPlace
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStation, MeteoSwissStationMeasurements

MINUTES_PER_HOUR = 60
# Up to a whole year, a leap year too
MAX_HISTORY_DAYS = 366


def _build_data_from_sentence(place: SwisstopoPlace, station: MeteoSwissStation) -> str:
    """Build the data_from sentence: which station measured the values, how far away and how much higher or lower."""
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


def _build_day_row(day_measurements: MeteoSwissStationMeasurements) -> Dict[str, Any]:
    """Build the answer row of one day of a station's daily measurements."""
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


def check_day_count(days: int) -> None:
    """Raise CannotAnswerError when days is not between 1 and MAX_HISTORY_DAYS."""
    if not 1 <= days <= MAX_HISTORY_DAYS:
        raise CannotAnswerError(f"days must be between 1 and {MAX_HISTORY_DAYS}, not {days}.")


def build_history_answer(
    meteoswiss_source: MeteoSwissMeasurementSource, place: SwisstopoPlace, first_day: date, days: int
) -> Dict[str, Any]:
    """
    Build the measurements_history answer for a number of UTC days from first_day; run it in a worker thread.

    Raise CannotAnswerError for today or later, and when no station has measurements for the days.
    """
    last_day = first_day + timedelta(days=days - 1)
    station_days = meteoswiss_source.find_station_days(place, first_day, last_day)

    day_rows = []
    for day_measurements in station_days:
        day_row = _build_day_row(day_measurements)
        day_rows.append(day_row)
    return {
        "data_from": _build_data_from_sentence(place, station_days[0].station),
        "place": place.name,
        "days": day_rows,
    }
