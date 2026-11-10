"""Build the measurements_extremes answer: where in Switzerland a measured value is at its extreme now."""
from typing import Any, Dict, List, Literal, NamedTuple

from . import parameters
from ..errors import CannotAnswerError
from ..formatting import format_swiss_time
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStation, MeteoSwissStationMeasurements


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


def build_extremes_answer(meteoswiss_source: MeteoSwissMeasurementSource, extreme: str) -> Dict[str, Any]:
    """
    Build the measurements_extremes answer; run it in a worker thread, as it downloads.

    extreme is one of EXTREME_NAME_TO_EXTREME. Raise CannotAnswerError for an unknown extreme or when
    MeteoSwiss publishes no measurements.
    """
    if extreme not in EXTREME_NAME_TO_EXTREME:
        extreme_names = ", ".join(EXTREME_NAME_TO_EXTREME)
        raise CannotAnswerError(f"'{extreme}' is not a known extreme, use one of: {extreme_names}.")

    all_measurements = meteoswiss_source.read_all_stations_latest_measurements()
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
