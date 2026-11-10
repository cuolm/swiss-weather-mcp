import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, NamedTuple, Optional, TypeVar

import requests

from . import parameters
from ..distance import GridPosition, calculate_distance_m, calculate_weighted_distance_m
from ..errors import CannotAnswerError
from ..formatting import SWISS_TZ, describe_height_difference, find_compass_point, format_swiss_time
from ..places import PlaceFinder, SwisstopoPlace
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStation, MeteoSwissStationMeasurements
from .slf_source import SLFMeasurementSource, SLFStation, SLFStationMeasurements
from .snow_values import NewSnow, SnowDepth, SnowStation

logger = logging.getLogger(__name__)

METRES_PER_KILOMETRE = 1000
MINUTES_PER_HOUR = 60
KMH_PER_M_PER_S = 3.6
# Up to a whole year, a leap year too
MAX_HISTORY_DAYS = 366

# The usual period for a pressure tendency
CHANGE_PERIOD = timedelta(hours=3)
LAST_HOUR = timedelta(hours=1)
# A station publishes a row every 10 minutes
MEASUREMENTS_PER_HOUR = 6
# As many as the sources try for the weather now
MAX_SNOW_STATIONS_TRIED = 3
# Snow changes with height and terrain, so a station farther than this by distance plus 20 times the
# height gap, such as 15 km away and 500 m higher, says too little about a place. Tested on the 5817
# forecast points: 94 % of those between 1200 and 2000 m keep a new snow station, and 88 % of those below
# 800 m, where the nearest SLF station is often 40 km away and 600 m higher, get none.
MAX_SNOW_STATION_WEIGHTED_DISTANCE_M = 25000

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
    station: MeteoSwissStation
    value: float


# Any station list, so ranking SLF stations alone still returns SLF stations
SnowStationT = TypeVar("SnowStationT", bound=GridPosition)


def _describe_station(place: SwisstopoPlace, station: MeteoSwissStation) -> str:
    """Say which station measured the values for a place, its distance and height difference."""
    distance_km = round(calculate_distance_m(place, station) / METRES_PER_KILOMETRE, 1)
    difference_m = round(station.altitude_m - place.altitude_m)
    return (
        f"The measurements for {place.display_name} are those of the MeteoSwiss station "
        f"{station.display_name}, {distance_km} km away and {describe_height_difference(difference_m)}."
    )


def _build_data_from_sentence(place: SwisstopoPlace, measurements: MeteoSwissStationMeasurements) -> str:
    """Build the data_from sentence: the station that measured the values, and when it measured them."""
    station_sentence = _describe_station(place, measurements.station)
    measured_at = measurements.measured_at.astimezone(SWISS_TZ)
    return f"{station_sentence} It measured the following values at {measured_at:%H:%M}."


def _build_slf_data_from_sentence(place: SwisstopoPlace, slf_measurements: SLFStationMeasurements) -> str:
    """Build the data_from sentence of the SLF block: the station, and the time its values cover."""
    station = slf_measurements.station
    distance_km = round(calculate_distance_m(place, station) / METRES_PER_KILOMETRE, 1)
    difference_m = round(station.altitude_m - place.altitude_m)
    measured_at = slf_measurements.measured_at.astimezone(SWISS_TZ)
    return (
        f"The SLF station {station.display_name}, {distance_km} km away and {describe_height_difference(difference_m)}, "
        f"suits {place.display_name} better in distance and height than the MeteoSwiss station. Its values "
        f"cover the 30 minutes up to {measured_at:%H:%M}."
    )


def _round_to_tenth(value: Optional[float]) -> Optional[float]:
    """Round a value to a tenth, as MeteoSwiss publishes them, or return None for None."""
    if value is None:
        return None
    return round(value, 1)


def _convert_m_per_s_to_kmh(speed_m_per_s: Optional[float]) -> Optional[float]:
    """Turn a speed in m/s into km/h rounded to a tenth, or return None for None."""
    if speed_m_per_s is None:
        return None
    return round(speed_m_per_s * KMH_PER_M_PER_S, 1)


def _build_slf_block(place: SwisstopoPlace, slf_measurements: SLFStationMeasurements) -> Dict[str, Any]:
    """Build the slf_station block of the weather now: the latest measurements of an SLF station."""
    wind_direction_degrees = _round_to_tenth(slf_measurements.wind_direction_degrees)
    if wind_direction_degrees is None:
        compass_point = None
    else:
        compass_point = find_compass_point(wind_direction_degrees)
    return {
        "data_from": _build_slf_data_from_sentence(place, slf_measurements),
        "measured_at": format_swiss_time(slf_measurements.measured_at),
        "temperature_c": _round_to_tenth(slf_measurements.temperature_c),
        "humidity_percent": _round_to_tenth(slf_measurements.humidity_percent),
        "wind_speed_kmh": _convert_m_per_s_to_kmh(slf_measurements.wind_speed_m_per_s),
        "wind_direction_degrees": wind_direction_degrees,
        "compass_point": compass_point,
        "strongest_gust_last_30_minutes_kmh": _convert_m_per_s_to_kmh(slf_measurements.strongest_gust_m_per_s),
    }


def _describe_distance_and_height(place: SwisstopoPlace, position: GridPosition) -> str:
    """Say how far away and how much higher or lower a station is, such as "0.5 km away and 30 m higher"."""
    distance_km = round(calculate_distance_m(place, position) / METRES_PER_KILOMETRE, 1)
    difference_m = round(position.altitude_m - place.altitude_m)
    return f"{distance_km} km away and {describe_height_difference(difference_m)}"


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
    distance_and_height = _describe_distance_and_height(place, snow_depth.station)
    measured_at = snow_depth.measured_at.astimezone(SWISS_TZ)
    data_from = (
        f"The snow depth for {place.display_name} {station_source}, {distance_and_height}. "
        f"It was measured at {measured_at:%H:%M} on {measured_at:%Y-%m-%d}."
    )
    return {
        "data_from": data_from,
        "measured_at": format_swiss_time(snow_depth.measured_at),
        "snow_depth_cm": _round_to_tenth(snow_depth.snow_depth_cm),
    }


def _build_new_snow_block(place: SwisstopoPlace, new_snow: NewSnow) -> Dict[str, Any]:
    """Build the new_snow block of the snow answer."""
    station_source = _describe_snow_station(new_snow.station)
    distance_and_height = _describe_distance_and_height(place, new_snow.station)
    measured_at = new_snow.measured_at.astimezone(SWISS_TZ)
    data_from = (
        f"The new snow for {place.display_name} {station_source}, {distance_and_height}. "
        f"It covers the 24 hours and the 3 days up to {measured_at:%H:%M} on {measured_at:%Y-%m-%d}."
    )
    return {
        "data_from": data_from,
        "measured_at": format_swiss_time(new_snow.measured_at),
        "new_snow_24_hours_cm": _round_to_tenth(new_snow.new_snow_24_hours_cm),
        "new_snow_3_days_cm": _round_to_tenth(new_snow.new_snow_3_days_cm),
    }


def _rank_close_snow_stations(place: SwisstopoPlace, snow_stations: List[SnowStationT]) -> List[SnowStationT]:
    """Rank the stations within MAX_SNOW_STATION_WEIGHTED_DISTANCE_M of a place, the closest first."""
    close_snow_stations = []
    for station in snow_stations:
        if calculate_weighted_distance_m(place, station) <= MAX_SNOW_STATION_WEIGHTED_DISTANCE_M:
            close_snow_stations.append(station)
    return sorted(close_snow_stations, key=lambda station: calculate_weighted_distance_m(place, station))


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


def _find_station_values(all_measurements: List[MeteoSwissStationMeasurements], parameter: str) -> List[StationValue]:
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
    """Answer what the stations measure: the weather and the snow at a place, past days, and all stations now."""

    def __init__(
        self,
        place_finder: PlaceFinder,
        meteoswiss_source: MeteoSwissMeasurementSource,
        slf_source: SLFMeasurementSource,
    ):
        self.place_finder = place_finder
        self.meteoswiss_source = meteoswiss_source
        self.slf_source = slf_source

    async def _find_place(self, location: str, nearby_location: Optional[str]) -> SwisstopoPlace:
        """Find the place a user means in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)

    async def _find_station_measurements(self, place: SwisstopoPlace) -> List[MeteoSwissStationMeasurements]:
        """Find a place's station measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.meteoswiss_source.find_station_measurements, place)

    async def _find_closer_slf_station_measurements(
        self, place: SwisstopoPlace, meteoswiss_station_weighted_distance_m: float
    ) -> Optional[SLFStationMeasurements]:
        """
        Find a place's closer SLF station measurements in a worker thread, so a download does not block other requests.

        Return None when no SLF station suits the place better than the MeteoSwiss station, and when SLF
        cannot be reached, as the MeteoSwiss values answer alone.
        """
        try:
            return await asyncio.to_thread(
                self.slf_source.find_closer_slf_station_measurements, place, meteoswiss_station_weighted_distance_m
            )
        except requests.RequestException as error:
            logger.warning(f"Could not download the SLF measurements for {place.display_name}, answering without: {error}")
            return None

    async def _find_station_days(
        self, place: SwisstopoPlace, first_day: date, last_day: date
    ) -> List[MeteoSwissStationMeasurements]:
        """Find a place's daily station measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.meteoswiss_source.find_station_days, place, first_day, last_day)

    async def _read_all_stations_latest_measurements(self) -> List[MeteoSwissStationMeasurements]:
        """Read every station's latest measurements in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.meteoswiss_source.read_all_stations_latest_measurements)

    def _list_slf_snow_stations(self) -> List[SLFStation]:
        """List the SLF snow stations, or none when SLF cannot be reached, as the MeteoSwiss stations answer alone."""
        try:
            return self.slf_source.list_snow_stations()
        except requests.RequestException as error:
            logger.warning(f"Could not download the SLF snow stations, answering without them: {error}")
            return []

    def _read_snow_depth(self, station: SnowStation) -> Optional[SnowDepth]:
        """Read a station's current snow depth from its source, or None when it has none or SLF cannot be reached."""
        if isinstance(station, MeteoSwissStation):
            return self.meteoswiss_source.read_snow_depth(station)
        try:
            return self.slf_source.read_snow_depth(station)
        except requests.RequestException as error:
            logger.warning(f"Could not download the snow depth of SLF station {station.code}: {error}")
            return None

    def _read_new_snow(self, station: SLFStation) -> Optional[NewSnow]:
        """Read an SLF station's new snow, or None when it has none or SLF cannot be reached."""
        try:
            return self.slf_source.read_new_snow(station)
        except requests.RequestException as error:
            logger.warning(f"Could not download the new snow of SLF station {station.code}: {error}")
            return None

    def _find_snow_depth(self, place: SwisstopoPlace, snow_stations: List[SnowStation]) -> Optional[SnowDepth]:
        """
        Find the current snow depth for a place from the given snow stations.

        The stations within MAX_SNOW_STATION_WEIGHTED_DISTANCE_M are ranked by distance plus
        METRES_PER_HEIGHT_METRE times the height gap, and the first of the first MAX_SNOW_STATIONS_TRIED
        with a current snow depth is taken.
        """
        ranked_snow_stations = _rank_close_snow_stations(place, snow_stations)
        for station in ranked_snow_stations[:MAX_SNOW_STATIONS_TRIED]:
            snow_depth = self._read_snow_depth(station)
            if snow_depth is not None:
                return snow_depth
            logger.info(f"Station {station.name} has no current snow depth, trying the next station")
        return None

    def _find_new_snow(self, place: SwisstopoPlace, slf_snow_stations: List[SLFStation]) -> Optional[NewSnow]:
        """
        Find the new snow for a place from the given SLF snow stations, the only ones that have it.

        The stations are chosen and ranked as for the snow depth, and the first of the first
        MAX_SNOW_STATIONS_TRIED with new snow from today or yesterday is taken.
        """
        ranked_snow_stations = _rank_close_snow_stations(place, slf_snow_stations)
        for station in ranked_snow_stations[:MAX_SNOW_STATIONS_TRIED]:
            new_snow = self._read_new_snow(station)
            if new_snow is not None:
                return new_snow
            logger.info(f"SLF station {station.code} has no current new snow, trying the next station")
        return None

    def _build_snow_answer(self, place: SwisstopoPlace) -> Dict[str, Any]:
        """Find the snow depth and the new snow for a place, each from its own station; run it in a worker thread."""
        meteoswiss_snow_stations = self.meteoswiss_source.list_snow_stations()
        slf_snow_stations = self._list_slf_snow_stations()
        # Both sources measure the snow depth, only SLF the new snow
        snow_depth_stations: List[SnowStation] = [*meteoswiss_snow_stations, *slf_snow_stations]
        snow_depth = self._find_snow_depth(place, snow_depth_stations)
        new_snow = self._find_new_snow(place, slf_snow_stations)
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

    async def read_current(self, location: str, nearby_location: Optional[str] = None) -> Dict[str, Any]:
        """
        Read the latest measurements of the station chosen for the location.

        Add the slf_station block when an SLF station suits the location better than the MeteoSwiss station.
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

        meteoswiss_station_weighted_distance_m = calculate_weighted_distance_m(place, latest_measurements.station)
        slf_measurements = await self._find_closer_slf_station_measurements(place, meteoswiss_station_weighted_distance_m)
        if slf_measurements is not None:
            answer["slf_station"] = _build_slf_block(place, slf_measurements)
        return answer

    async def read_history(
        self, location: str, first_day: date, days: int, nearby_location: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Read the daily measurements of the station chosen for the location, for 1 to MAX_HISTORY_DAYS UTC days from first_day.

        Raise CannotAnswerError when days is out of range, for an unknown location, for today or later, and
        when no station has measurements for the days.
        """
        if not 1 <= days <= MAX_HISTORY_DAYS:
            raise CannotAnswerError(f"days must be between 1 and {MAX_HISTORY_DAYS}, not {days}.")

        last_day = first_day + timedelta(days=days - 1)
        place = await self._find_place(location, nearby_location)
        station_days = await self._find_station_days(place, first_day, last_day)

        day_rows = []
        for day_measurements in station_days:
            day_row = _build_day_row(day_measurements)
            day_rows.append(day_row)
        return {
            "data_from": _describe_station(place, station_days[0].station),
            "place": place.name,
            "days": day_rows,
        }

    async def read_snow(self, location: str, nearby_location: Optional[str] = None) -> Dict[str, Any]:
        """
        Read the current snow depth and the new snow for the location, each from its own best station.

        snow_depth or new_snow is None when no station close enough has a current value. Raise
        CannotAnswerError for an unknown location and when neither has one.
        """
        place = await self._find_place(location, nearby_location)
        return await asyncio.to_thread(self._build_snow_answer, place)

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
