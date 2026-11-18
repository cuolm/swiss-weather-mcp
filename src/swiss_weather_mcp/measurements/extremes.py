"""Build the measurements_extremes answer: where in Switzerland a value is at its extreme now or this morning."""
from typing import Any, Literal, NamedTuple, NotRequired, Optional, TypedDict, Union

from ..errors import CannotAnswerError
from ..formatting import describe_source, format_swiss_time
from . import parameters
from .meteoswiss_source import MeteoSwissMeasurementSource, MeteoSwissStation, MeteoSwissStationMeasurements
from .slf_source import SLFMeasurementSource, SLFMorningSnow, SLFStation


class MeteoSwissExtreme(NamedTuple):
    """The value of MeteoSwiss's file of all stations an extreme ranks the stations by."""
    parameter: str
    answer_key: str  # the key the value fills in a station row
    highest_first: bool


class SLFExtreme(NamedTuple):
    """The morning snow value an extreme ranks the SLF stations by."""
    answer_key: str  # the key the value fills in a station row
    is_new_snow: bool  # the new snow of the 24 hours up to the morning; otherwise the snow depth


ExtremeName = Literal["warmest", "coldest", "windiest", "wettest", "sunniest", "deepest_snow", "most_new_snow"]
EXTREME_NAME_TO_EXTREME: dict[ExtremeName, Union[MeteoSwissExtreme, SLFExtreme]] = {
    "warmest": MeteoSwissExtreme(parameters.TEMPERATURE, "temperature_c", highest_first=True),
    "coldest": MeteoSwissExtreme(parameters.TEMPERATURE, "temperature_c", highest_first=False),
    "windiest": MeteoSwissExtreme(parameters.WIND_GUSTS, "gusts_kmh", highest_first=True),
    "wettest": MeteoSwissExtreme(parameters.PRECIPITATION, "rain_last_10_minutes_mm", highest_first=True),
    "sunniest": MeteoSwissExtreme(parameters.SUNSHINE, "sunshine_last_10_minutes_minutes", highest_first=True),
    # MeteoSwiss publishes the snow of each station in its own file, so only SLF's answers of all stations are
    # ranked
    "deepest_snow": SLFExtreme("snow_depth_cm", is_new_snow=False),
    "most_new_snow": SLFExtreme("new_snow_24_hours_cm", is_new_snow=True),
}
# How many stations a ranking lists
RANKED_STATION_COUNT = 5
# The smallest rain MeteoSwiss publishes, as the 10-minute rain has one decimal
RAINY_MIN_RAIN_MM = 0.1
# The rankings name stations and cantons, no swisstopo place
METEOSWISS_EXTREMES_SOURCE = describe_source(uses_meteoswiss=True, uses_slf=False, uses_swisstopo=False)
SLF_EXTREMES_SOURCE = describe_source(uses_meteoswiss=False, uses_slf=True, uses_swisstopo=False)


class SunshineClass(NamedTuple):
    """One class of MeteoSwiss's sunshine terms."""
    name: str
    min_sunshine_minutes: int  # the least sun in the last 10 minutes for the class


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


class MeteoSwissStationValue(NamedTuple):
    """One measured value of one station."""
    station: MeteoSwissStation
    value: float


class SLFStationValue(NamedTuple):
    """One morning snow value of one SLF station."""
    station: SLFStation
    value: float


class ExtremesRanking(TypedDict):
    """
    The ranking of the measurements_extremes answer.

    The rows stay plain dicts, as some of their keys come from the data: the field of the extreme in a
    station row, and the sunshine classes in a canton row of the sunniest.
    """
    extreme: str
    measured_at: str
    rainy_stations: NotRequired[int]
    measuring_stations: NotRequired[int]
    stations_with_snow: NotRequired[int]
    stations_by_sunshine: NotRequired[dict[str, int]]
    cantons: NotRequired[list[dict[str, Any]]]
    stations: NotRequired[list[dict[str, Any]]]


class ExtremesAnswer(ExtremesRanking):
    """The measurements_extremes answer."""
    source: str


def _read_snow_value(morning_snow: SLFMorningSnow, slf_extreme: SLFExtreme) -> Optional[float]:
    """Read the value of a station's morning snow that a snow extreme ranks by."""
    if slf_extreme.is_new_snow:
        return morning_snow.new_snow_cm
    return morning_snow.snow_depth_cm


def _find_slf_station_values(
    all_morning_snow: list[SLFMorningSnow], slf_extreme: SLFExtreme
) -> list[SLFStationValue]:
    """Find the stations that have the value a snow extreme ranks by, so a station without one is never counted."""
    slf_station_values = []
    for morning_snow in all_morning_snow:
        value = _read_snow_value(morning_snow, slf_extreme)
        if value is not None:
            slf_station_value = SLFStationValue(morning_snow.station, value)
            slf_station_values.append(slf_station_value)
    return slf_station_values


def _describe_station_kind(station: SLFStation, slf_extreme: SLFExtreme) -> str:
    """Say what kind of station a value is from, and for the new snow whether it was measured or modelled."""
    if station.is_manual_site:
        kind = "manual SLF site"
        method = "measured"
    else:
        kind = "automatic SLF station"
        # SLF calculates the new snow of its automatic stations from the snow depth with its SNOWPACK model
        method = "modelled"
    if slf_extreme.is_new_snow:
        return f"{kind}, {method}"
    return kind


def _build_slf_station_rows(
    snowy_values: list[SLFStationValue], slf_extreme: SLFExtreme
) -> list[dict[str, Any]]:
    """Build the answer rows of the first RANKED_STATION_COUNT stations, the most snow first."""
    ranked_values = sorted(snowy_values, key=lambda slf_station_value: (
        -slf_station_value.value, slf_station_value.station.name
    ))
    rows = []
    for slf_station_value in ranked_values[:RANKED_STATION_COUNT]:
        kind = _describe_station_kind(slf_station_value.station, slf_extreme)
        rows.append({
            "station": slf_station_value.station.display_name,
            "canton": slf_station_value.station.canton,
            "kind": kind,
            slf_extreme.answer_key: slf_station_value.value,
        })
    return rows


def _build_snow_extremes_answer(
    slf_source: SLFMeasurementSource, extreme_name: str, slf_extreme: SLFExtreme
) -> ExtremesAnswer:
    """Build the answer of a snow extreme from the morning snow of the SLF stations."""
    all_morning_snow = slf_source.read_morning_snow()
    if not all_morning_snow:
        raise CannotAnswerError("SLF currently publishes no daily snow, try again later.")
    slf_station_values = _find_slf_station_values(all_morning_snow, slf_extreme)
    snowy_values = []
    for slf_station_value in slf_station_values:
        if slf_station_value.value > 0:
            snowy_values.append(slf_station_value)
    return {
        "extreme": extreme_name,
        # Every station's value is of the same morning
        "measured_at": format_swiss_time(all_morning_snow[0].measured_at),
        "measuring_stations": len(slf_station_values),
        "stations_with_snow": len(snowy_values),
        "stations": _build_slf_station_rows(snowy_values, slf_extreme),
        "source": SLF_EXTREMES_SOURCE,
    }


def _find_station_values(
    all_measurements: list[MeteoSwissStationMeasurements], parameter: str
) -> list[MeteoSwissStationValue]:
    """Find the stations that have a value for a parameter, so a station without one is never counted."""
    station_values = []
    for measurements in all_measurements:
        value = measurements.parameter_to_value[parameter]
        if value is not None:
            station_value = MeteoSwissStationValue(measurements.station, value)
            station_values.append(station_value)
    return station_values


def _find_sunshine_class(sunshine_minutes: float) -> str:
    """Find the class of MeteoSwiss's sunshine terms for the sun of the last 10 minutes."""
    for sunshine_class in SUNSHINE_CLASSES[:-1]:
        if sunshine_minutes >= sunshine_class.min_sunshine_minutes:
            return sunshine_class.name
    # The last class takes the rest, down to 0 minutes
    return SUNSHINE_CLASSES[-1].name


def _count_stations_by_sunshine_class(station_values: list[MeteoSwissStationValue]) -> dict[str, int]:
    """Count the stations in each class of MeteoSwiss's sunshine terms, sunniest first."""
    sunshine_class_to_station_count = {}
    for sunshine_class in SUNSHINE_CLASSES:
        sunshine_class_to_station_count[sunshine_class.name] = 0
    for station_value in station_values:
        sunshine_class_name = _find_sunshine_class(station_value.value)
        sunshine_class_to_station_count[sunshine_class_name] += 1
    return sunshine_class_to_station_count


def _build_sunshine_canton_rows(station_values: list[MeteoSwissStationValue]) -> list[dict[str, Any]]:
    """
    Build one answer row for each canton with a measuring station, with its stations in each sunshine class.

    The canton whose stations had the most sun on average comes first.
    """
    canton_to_station_values: dict[str, list[MeteoSwissStationValue]] = {}
    for station_value in station_values:
        canton = station_value.station.canton
        canton_to_station_values.setdefault(canton, []).append(station_value)

    rows: list[dict[str, Any]] = []
    canton_to_average_sunshine_minutes: dict[str, float] = {}
    for canton, canton_values in canton_to_station_values.items():
        rows.append({
            "canton": canton,
            "measuring_stations": len(canton_values),
            **_count_stations_by_sunshine_class(canton_values),
        })
        total_sunshine_minutes = sum(station_value.value for station_value in canton_values)
        canton_to_average_sunshine_minutes[canton] = total_sunshine_minutes / len(canton_values)

    # Among equal averages the canton with more stations comes first, because its average is better supported
    rows.sort(key=lambda row: (
        -canton_to_average_sunshine_minutes[row["canton"]], -row["measuring_stations"], row["canton"]
    ))
    return rows


def _find_station_values_at_least(
    station_values: list[MeteoSwissStationValue], minimum: float
) -> list[MeteoSwissStationValue]:
    """Find the stations whose value is at least a minimum."""
    matching_values = []
    for station_value in station_values:
        if station_value.value >= minimum:
            matching_values.append(station_value)
    return matching_values


def _count_stations_by_canton(station_values: list[MeteoSwissStationValue]) -> dict[str, int]:
    """Count the stations of each canton."""
    canton_to_station_count: dict[str, int] = {}
    for station_value in station_values:
        canton = station_value.station.canton
        canton_to_station_count[canton] = canton_to_station_count.get(canton, 0) + 1
    return canton_to_station_count


def _build_canton_rows(
    station_values: list[MeteoSwissStationValue], matching_values: list[MeteoSwissStationValue], label: str
) -> list[dict[str, Any]]:
    """
    Build one answer row for each canton with a measuring station.

    Each row says how many of its measuring stations match, such as the rainy ones. The canton with
    the highest share comes first.
    """
    canton_to_measuring_count = _count_stations_by_canton(station_values)
    canton_to_matching_count = _count_stations_by_canton(matching_values)
    rows: list[dict[str, Any]] = []
    for canton, measuring_count in canton_to_measuring_count.items():
        matching_count = canton_to_matching_count.get(canton, 0)
        rows.append({
            "canton": canton,
            f"{label}_stations": matching_count,
            "measuring_stations": measuring_count,
            f"{label}_percent": round(100 * matching_count / measuring_count),
        })
    # Among equal shares the canton with more stations comes first, because its share is better supported
    rows.sort(key=lambda row: (-row[f"{label}_percent"], -row["measuring_stations"], row["canton"]))
    return rows


def _build_station_rows(
    station_values: list[MeteoSwissStationValue], extreme: MeteoSwissExtreme
) -> list[dict[str, Any]]:
    """Build the answer rows of the first RANKED_STATION_COUNT stations in the order of an extreme."""
    ranked_values = sorted(
        station_values, key=lambda station_value: station_value.value, reverse=extreme.highest_first
    )
    rows = []
    for station_value in ranked_values[:RANKED_STATION_COUNT]:
        rows.append({
            "station": station_value.station.display_name,
            "canton": station_value.station.canton,
            extreme.answer_key: station_value.value,
        })
    return rows


def build_extremes_answer(
    meteoswiss_source: MeteoSwissMeasurementSource, slf_source: SLFMeasurementSource, extreme_name: str
) -> ExtremesAnswer:
    """
    Build the measurements_extremes answer; run it in a worker thread, as it downloads.

    extreme_name is one of EXTREME_NAME_TO_EXTREME. Raise CannotAnswerError for an unknown extreme, and when
    MeteoSwiss, or SLF for the snow, publishes no measurements.
    """
    if extreme_name not in EXTREME_NAME_TO_EXTREME:
        extreme_names = ", ".join(EXTREME_NAME_TO_EXTREME)
        raise CannotAnswerError(f"'{extreme_name}' is not a known extreme, use one of: {extreme_names}.")
    extreme = EXTREME_NAME_TO_EXTREME[extreme_name]
    if isinstance(extreme, SLFExtreme):
        return _build_snow_extremes_answer(slf_source, extreme_name, extreme)

    all_measurements = meteoswiss_source.read_all_stations_latest_measurements()
    if not all_measurements:
        raise CannotAnswerError("MeteoSwiss currently publishes no measurements, try again later.")

    station_values = _find_station_values(all_measurements, extreme.parameter)
    ranking: ExtremesRanking = {
        "extreme": extreme_name,
        # Every row of the all stations file has the same time
        "measured_at": format_swiss_time(all_measurements[0].measured_at),
    }
    if extreme_name == "sunniest":
        ranking["measuring_stations"] = len(station_values)
        ranking["stations_by_sunshine"] = _count_stations_by_sunshine_class(station_values)
        ranking["cantons"] = _build_sunshine_canton_rows(station_values)
    elif extreme_name == "wettest":
        rainy_values = _find_station_values_at_least(station_values, RAINY_MIN_RAIN_MM)
        ranking["rainy_stations"] = len(rainy_values)
        ranking["measuring_stations"] = len(station_values)
        ranking["cantons"] = _build_canton_rows(station_values, rainy_values, "rainy")
        ranking["stations"] = _build_station_rows(rainy_values, extreme)
    else:
        ranking["stations"] = _build_station_rows(station_values, extreme)
    return {**ranking, "source": METEOSWISS_EXTREMES_SOURCE}
