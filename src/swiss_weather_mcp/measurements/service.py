import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Literal, NamedTuple, Optional

from . import parameters
from ..errors import CannotAnswerError
from ..formatting import SWISS_TZ, describe_height_difference, find_compass_point, format_swiss_time
from ..places import PlaceFinder, SwisstopoPlace
from .source import MeasurementSource, Station, StationMeasurements, calculate_distance_m

logger = logging.getLogger(__name__)

METRES_PER_KILOMETRE = 1000

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


class Extreme(NamedTuple):
    """The measured value an extreme ranks the stations by."""
    parameter: str
    field: str  # the field the value fills in the answer
    highest_first: bool


ExtremeName = Literal["warmest", "coldest", "windiest", "wettest", "sunniest"]
EXTREME_NAME_TO_EXTREME: Dict[ExtremeName, Extreme] = {
    "warmest": Extreme(parameters.TEMPERATURE, "temperature_c", highest_first=True),
    "coldest": Extreme(parameters.TEMPERATURE, "temperature_c", highest_first=False),
    "windiest": Extreme(parameters.WIND_GUSTS, "gusts_kmh", highest_first=True),
    "wettest": Extreme(parameters.PRECIPITATION, "rain_last_10_minutes_mm", highest_first=True),
    "sunniest": Extreme(parameters.SUNSHINE, "sunshine_last_10_minutes_min", highest_first=True),
}
# How many stations a ranking lists
STATION_COUNT = 5
# The smallest rain MeteoSwiss publishes, as the 10-minute rain has one decimal
RAINY_MIN_RAIN_MM = 0.1


class SunshineClass(NamedTuple):
    """One class of MeteoSwiss's sunshine terms."""
    name: str
    min_sunshine_min: int  # the least sun in the last 10 minutes for the class


# MeteoSwiss's sunshine terms by the share of sun, 80-100 %, 50-80 %, 20-50 %, 5-20 % and 0-5 %,
# https://www.meteoswiss.admin.ch/weather/weather-and-climate-from-a-to-z/sunshine.html
# They are applied to the sun in the last 10 minutes, which MeteoSwiss publishes in whole minutes, so
# 1 minute is 10 %. A share on the boundary of two classes, such as 50 %, belongs to the sunnier one.
SUNSHINE_CLASSES = (
    SunshineClass("mostly_sunny", 8),
    SunshineClass("rather_sunny", 5),
    SunshineClass("partly_sunny", 2),
    SunshineClass("only_a_little_sunshine", 1),
    SunshineClass("hardly_any_sunshine", 0),
)


class StationValue(NamedTuple):
    """One measured value of one station."""
    station: Station
    value: float


def _build_data_from_sentence(place: SwisstopoPlace, measurements: StationMeasurements) -> str:
    """Build the data_from sentence: the station that measured the values, its distance and height difference."""
    station = measurements.station
    distance_km = round(calculate_distance_m(place, station) / METRES_PER_KILOMETRE, 1)
    difference_m = round(station.altitude_m - place.altitude_m)
    measured_at = measurements.measured_at.astimezone(SWISS_TZ)
    return (
        f"The measurements for {place.display_name} are those of the MeteoSwiss station "
        f"{station.display_name}, {distance_km} km away and {describe_height_difference(difference_m)}. "
        f"It measured the following values at {measured_at:%H:%M}."
    )


def _find_measurements_at(all_measurements: List[StationMeasurements],
                          measured_at: datetime) -> Optional[StationMeasurements]:
    """Find the measurements of a time, or None when the station published none for it."""
    for measurements in all_measurements:
        if measurements.measured_at == measured_at:
            return measurements
    return None


def _calculate_change(all_measurements: List[StationMeasurements], parameter: str) -> Optional[float]:
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


def _find_last_hour_values(all_measurements: List[StationMeasurements], parameter: str) -> Optional[List[float]]:
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


def _sum_last_hour(all_measurements: List[StationMeasurements], parameter: str) -> Optional[float]:
    """Return the total of a 10-minute value over the last hour, such as the rain, or None when one is missing."""
    values = _find_last_hour_values(all_measurements, parameter)
    if values is None:
        return None
    # Rounded, as adding tenths of a millimetre leaves float noise such as 0.30000000000000004
    return round(sum(values), 1)


def _find_highest_last_hour(all_measurements: List[StationMeasurements], parameter: str) -> Optional[float]:
    """Return the highest 10-minute value of the last hour, such as the strongest gust, or None when one is missing."""
    values = _find_last_hour_values(all_measurements, parameter)
    if values is None:
        return None
    return max(values)


def _find_station_values(all_measurements: List[StationMeasurements], parameter: str) -> List[StationValue]:
    """Find the stations that have a value for a parameter, so a station without one is never counted."""
    station_values = []
    for measurements in all_measurements:
        value = measurements.parameter_to_value[parameter]
        if value is not None:
            station_values.append(StationValue(measurements.station, value))
    return station_values


def _find_station_values_from(station_values: List[StationValue], minimum: float) -> List[StationValue]:
    """Find the stations whose value is at least a minimum."""
    matching_values = []
    for station_value in station_values:
        if station_value.value >= minimum:
            matching_values.append(station_value)
    return matching_values


def _build_station_rows(station_values: List[StationValue], extreme: Extreme) -> List[Dict[str, Any]]:
    """Build the answer rows of the first 5 stations in the order of an extreme."""
    ranked_values = sorted(
        station_values, key=lambda station_value: station_value.value, reverse=extreme.highest_first
    )
    rows = []
    for station_value in ranked_values[:STATION_COUNT]:
        rows.append({
            "station": station_value.station.display_name,
            "canton": station_value.station.canton,
            extreme.field: station_value.value,
        })
    return rows


def _count_stations_by_canton(station_values: List[StationValue]) -> Dict[str, int]:
    """Count the stations of each canton."""
    canton_to_station_count: Dict[str, int] = {}
    for station_value in station_values:
        canton = station_value.station.canton
        canton_to_station_count[canton] = canton_to_station_count.get(canton, 0) + 1
    return canton_to_station_count


def _build_canton_rows(station_values: List[StationValue], matching_values: List[StationValue],
                       label: str) -> List[Dict[str, Any]]:
    """
    Build one answer row for each canton with a measuring station.

    Each row says how many of its measuring stations match, such as the rainy ones. The canton with
    the highest share comes first.
    """
    measuring_counts = _count_stations_by_canton(station_values)
    matching_counts = _count_stations_by_canton(matching_values)
    rows: List[Dict[str, Any]] = []
    for canton, measuring_count in measuring_counts.items():
        matching_count = matching_counts.get(canton, 0)
        rows.append({
            "canton": canton,
            f"{label}_stations": matching_count,
            "measuring_stations": measuring_count,
            f"{label}_percent": round(100 * matching_count / measuring_count),
        })
    # Among equal shares the canton with more stations comes first, because its share is better supported
    rows.sort(key=lambda row: (-row[f"{label}_percent"], -row["measuring_stations"], row["canton"]))
    return rows


def _find_sunshine_class(sunshine_min: float) -> str:
    """Find the class of MeteoSwiss's sunshine terms for the sun of the last 10 minutes."""
    for sunshine_class in SUNSHINE_CLASSES[:-1]:
        if sunshine_min >= sunshine_class.min_sunshine_min:
            return sunshine_class.name
    # The last class takes the rest, down to 0 minutes
    return SUNSHINE_CLASSES[-1].name


def _count_stations_by_sunshine_class(station_values: List[StationValue]) -> Dict[str, int]:
    """Count the stations in each class of MeteoSwiss's sunshine terms, sunniest first."""
    sunshine_class_to_station_count = {}
    for sunshine_class in SUNSHINE_CLASSES:
        sunshine_class_to_station_count[sunshine_class.name] = 0
    for station_value in station_values:
        sunshine_class_name = _find_sunshine_class(station_value.value)
        sunshine_class_to_station_count[sunshine_class_name] += 1
    return sunshine_class_to_station_count


def _build_sunshine_canton_rows(station_values: List[StationValue]) -> List[Dict[str, Any]]:
    """
    Build one answer row for each canton with a measuring station, with its stations in each sunshine class.

    The canton whose stations had the most sun on average comes first.
    """
    canton_to_station_values: Dict[str, List[StationValue]] = {}
    for station_value in station_values:
        canton = station_value.station.canton
        canton_to_station_values.setdefault(canton, []).append(station_value)

    rows: List[Dict[str, Any]] = []
    canton_to_average_sunshine_min: Dict[str, float] = {}
    for canton, canton_values in canton_to_station_values.items():
        rows.append({
            "canton": canton,
            "measuring_stations": len(canton_values),
            **_count_stations_by_sunshine_class(canton_values),
        })
        total_sunshine_min = sum(station_value.value for station_value in canton_values)
        canton_to_average_sunshine_min[canton] = total_sunshine_min / len(canton_values)

    # Among equal averages the canton with more stations comes first, because its average is better supported
    rows.sort(key=lambda row: (
        -canton_to_average_sunshine_min[row["canton"]], -row["measuring_stations"], row["canton"]
    ))
    return rows


class MeasurementService:
    """Answer what the weather is now, at a place and across all stations, from the station measurements."""

    def __init__(self, place_finder: PlaceFinder, measurement_source: MeasurementSource):
        self.place_finder = place_finder
        self.measurement_source = measurement_source

    async def _find_place(self, location: str, nearby_location: Optional[str]) -> SwisstopoPlace:
        """Find the place a user means in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)

    async def _find_station_measurements(self, place: SwisstopoPlace) -> List[StationMeasurements]:
        """Find a place's station measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.measurement_source.find_station_measurements, place)

    async def _read_all_stations_latest_measurements(self) -> List[StationMeasurements]:
        """Read every station's latest measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.measurement_source.read_all_stations_latest_measurements)

    async def read_current(self, location: str, nearby_location: Optional[str] = None) -> Dict[str, Any]:
        """
        Read the latest measurements of the station chosen for the location.

        Raise CannotAnswerError for an unknown location or when no station publishes a temperature.
        """
        place = await self._find_place(location, nearby_location)
        all_measurements = await self._find_station_measurements(place)
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
        return answer

    async def read_extremes(self, extreme: str) -> Dict[str, Any]:
        """
        Read where in Switzerland a measured value is at its extreme now.

        extreme is one of EXTREME_NAME_TO_EXTREME. Raise CannotAnswerError for an unknown extreme or when
        MeteoSwiss publishes no measurements.
        """
        if extreme not in EXTREME_NAME_TO_EXTREME:
            extreme_names = ", ".join(EXTREME_NAME_TO_EXTREME)
            raise CannotAnswerError(f"'{extreme}' is not a known extreme, use one of: {extreme_names}.")

        all_measurements = await self._read_all_stations_latest_measurements()
        if not all_measurements:
            raise CannotAnswerError("MeteoSwiss currently publishes no measurements, try again later.")

        ranked_by = EXTREME_NAME_TO_EXTREME[extreme]
        station_values = _find_station_values(all_measurements, ranked_by.parameter)
        answer: Dict[str, Any] = {
            "extreme": extreme,
            # Every row of the all stations file has the same time
            "measured_at": format_swiss_time(all_measurements[0].measured_at),
        }
        if extreme == "sunniest":
            answer["measuring_stations"] = len(station_values)
            answer["stations_by_sunshine"] = _count_stations_by_sunshine_class(station_values)
            answer["cantons"] = _build_sunshine_canton_rows(station_values)
        elif extreme == "wettest":
            rainy_values = _find_station_values_from(station_values, RAINY_MIN_RAIN_MM)
            answer["rainy_stations"] = len(rainy_values)
            answer["measuring_stations"] = len(station_values)
            answer["cantons"] = _build_canton_rows(station_values, rainy_values, "rainy")
            answer["stations"] = _build_station_rows(rainy_values, ranked_by)
        else:
            answer["stations"] = _build_station_rows(station_values, ranked_by)
        return answer
