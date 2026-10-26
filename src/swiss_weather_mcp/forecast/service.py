import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import parameters
from ..errors import CannotAnswerError
from ..formatting import describe_height_difference, find_compass_point, format_swiss_time
from ..places import PlaceFinder, SwisstopoPlace
from .source import ForecastSeries, ForecastSource, MeteoSwissPoint, calculate_distance_m

logger = logging.getLogger(__name__)

# MeteoSwiss publishes today and the next 8 days
MAX_DAYS = 9
METRES_PER_KILOMETRE = 1000
# A forecast point this close forecasts the place itself, so the answer does not name it in data_from.
# 50 m of height changes the temperature by about 0.3 °C.
CLOSE_POINT_MAX_DISTANCE_KM = 1.0
CLOSE_POINT_MAX_HEIGHT_DIFFERENCE_M = 50

HOUR = timedelta(hours=1)
RAIN_BLOCK = timedelta(hours=3)


def _check_full_hour(moment: datetime) -> None:
    """Refuse a time that is not a full hour, as MeteoSwiss forecasts only whole hours."""
    if moment.minute or moment.second or moment.microsecond:
        full_hour = moment.replace(minute=0, second=0, microsecond=0)
        raise CannotAnswerError(
            f"{format_swiss_time(moment)} is not a full hour. MeteoSwiss forecasts whole hours, so use a "
            f"full hour such as {format_swiss_time(full_hour)} or {format_swiss_time(full_hour + HOUR)}."
        )


def _check_period_order(start_moment: datetime, end_moment: datetime) -> None:
    """Refuse a period whose end is not after its start."""
    if end_moment <= start_moment:
        raise CannotAnswerError(
            f"The end of the period, {format_swiss_time(end_moment)}, must be after its start, "
            f"{format_swiss_time(start_moment)}."
        )


def _create_stamps(first_stamp: datetime, last_stamp: datetime, step: timedelta) -> List[datetime]:
    """Return stamps one step apart, from the first until one reaches the last."""
    stamps = [first_stamp]
    while stamps[-1] < last_stamp:
        stamps.append(stamps[-1] + step)
    return stamps


def _describe_covered_range(series: ForecastSeries) -> str:
    """Say which times a series covers, for an error message."""
    return (
        f"The forecast covers {format_swiss_time(min(series.values))} to "
        f"{format_swiss_time(max(series.values))}."
    )


def _describe_pictogram(pictogram_code: int) -> Tuple[str, Optional[str]]:
    """Turn a MeteoSwiss pictogram code into the sentence it stands for and its emoji."""
    pictogram = parameters.PICTOGRAMS.get(pictogram_code)
    if pictogram is None:
        return f"unknown weather code {pictogram_code}", None
    return pictogram


def _get_daily_value(series: Optional[ForecastSeries], day: date) -> Optional[float]:
    """Return the value of a daily series for one day, or None when the series or the day is missing."""
    if series is None:
        return None
    # A daily row is stamped 00:00 on the Swiss calendar day it describes
    day_stamp = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return series.values.get(day_stamp)


def _get_hourly_value(series: ForecastSeries, moment: datetime, point: MeteoSwissPoint) -> float:
    """
    Return the value of an hourly series at a full hour.

    It is the average or sum of the hour up to it, or a snapshot. Raise CannotAnswerError when the
    forecast does not cover that hour.
    """
    if moment not in series.values:
        raise CannotAnswerError(
            f"{format_swiss_time(moment)} is outside the forecast for {point.display_name}. "
            f"{_describe_covered_range(series)}"
        )
    return series.values[moment]


def _build_forecast_point_fields(place: SwisstopoPlace, point: MeteoSwissPoint) -> Dict[str, Any]:
    """
    Build the answer fields that name the place, and the forecast point when it is not next to it.

    Only a point more than CLOSE_POINT_MAX_DISTANCE_KM away or more than
    CLOSE_POINT_MAX_HEIGHT_DIFFERENCE_M higher or lower gets data_from. A model given any word about
    a close point mentions it anyway.
    """
    distance_km = round(calculate_distance_m(place, point) / METRES_PER_KILOMETRE, 1)
    difference_m = round(point.altitude_m - place.altitude_m)
    if distance_km <= CLOSE_POINT_MAX_DISTANCE_KM and abs(difference_m) <= CLOSE_POINT_MAX_HEIGHT_DIFFERENCE_M:
        return {"place": place.name}
    return {
        "data_from": (
            f"The forecast for {place.display_name} is the one MeteoSwiss makes for its forecast point "
            f"{point.display_name}, {distance_km} km away and {describe_height_difference(difference_m)}."
        ),
        "place": place.name,
    }


class ForecastService:
    """Answer weather questions for a place: the value, its unit, the forecast point and the model run."""

    def __init__(self, place_finder: PlaceFinder, forecast_source: ForecastSource):
        self.place_finder = place_finder
        self.forecast_source = forecast_source

    async def _find_place_and_point(
        self, location: str, nearby_location: Optional[str]
    ) -> Tuple[SwisstopoPlace, MeteoSwissPoint]:
        """Find the place and its forecast point in a worker thread, so a download does not block other requests."""
        place = await asyncio.to_thread(self.place_finder.find_place, location, nearby_location)
        point = await asyncio.to_thread(self.forecast_source.find_forecast_point, place)
        return place, point

    async def _read_series(self, parameter: str, point: MeteoSwissPoint) -> ForecastSeries:
        """Read one parameter for one point in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.forecast_source.read_series, parameter, point)

    async def _read_daily_series_if_published(self, parameter: str, point: MeteoSwissPoint) -> Optional[ForecastSeries]:
        """Read one parameter for one point, or return None when MeteoSwiss does not publish it there."""
        try:
            return await self._read_series(parameter, point)
        except CannotAnswerError as error:
            logger.info(f"No daily {parameter} for {point.display_name}: {error}")
            return None

    async def read_daily(
        self, location: str, first_day: date, days: int, nearby_location: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Read the whole-day forecast for 1 to MAX_DAYS Swiss calendar days in a row from first_day.

        A value MeteoSwiss does not publish for the forecast point is None. Raise CannotAnswerError
        when days is out of range or no day has a forecast.
        """
        if not 1 <= days <= MAX_DAYS:
            raise CannotAnswerError(f"days must be between 1 and {MAX_DAYS}, not {days}.")

        place, point = await self._find_place_and_point(location, nearby_location)
        min_series, max_series, rain_series, rain_lower_series, rain_upper_series, pictogram_series = await asyncio.gather(
            self._read_daily_series_if_published(parameters.TEMPERATURE_DAY_MIN, point),
            self._read_daily_series_if_published(parameters.TEMPERATURE_DAY_MAX, point),
            self._read_daily_series_if_published(parameters.PRECIPITATION_DAY, point),
            self._read_daily_series_if_published(parameters.PRECIPITATION_DAY_Q10, point),
            self._read_daily_series_if_published(parameters.PRECIPITATION_DAY_Q90, point),
            self._read_daily_series_if_published(parameters.WEATHER_PICTOGRAM_DAY, point),
        )

        rows = []
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            temperature_min_c = _get_daily_value(min_series, day)
            temperature_max_c = _get_daily_value(max_series, day)
            rainfall_median_mm = _get_daily_value(rain_series, day)
            rainfall_lower_mm = _get_daily_value(rain_lower_series, day)
            rainfall_upper_mm = _get_daily_value(rain_upper_series, day)
            pictogram_value = _get_daily_value(pictogram_series, day)
            day_values = (
                temperature_min_c, temperature_max_c, rainfall_median_mm, rainfall_lower_mm, rainfall_upper_mm,
                pictogram_value,
            )
            if all(value is None for value in day_values):
                continue

            # The pictogram is published as a code, which is only useful once it is spelled out
            pictogram_code: Optional[int] = None
            weather: Optional[str] = None
            weather_emoji: Optional[str] = None
            if pictogram_value is not None:
                pictogram_code = int(pictogram_value)
                weather, weather_emoji = _describe_pictogram(pictogram_code)

            row = {
                "date": day.isoformat(),
                "weekday": f"{day:%A}",
                "temperature_min_c": temperature_min_c,
                "temperature_max_c": temperature_max_c,
                "rainfall_median_mm": rainfall_median_mm,
                "rainfall_10th_percentile_mm": rainfall_lower_mm,
                "rainfall_90th_percentile_mm": rainfall_upper_mm,
                "pictogram_code": pictogram_code,
                "weather": weather,
                "weather_emoji": weather_emoji,
            }
            rows.append(row)

        # All series come from the same model run, so any published one gives its time
        run_time: Optional[datetime] = None
        for series in (min_series, max_series, rain_series, rain_lower_series, rain_upper_series, pictogram_series):
            if series is not None:
                run_time = series.run_time
                break
        if not rows or run_time is None:
            raise CannotAnswerError(f"MeteoSwiss has no daily forecast for {point.display_name} from {first_day.isoformat()}.")
        return {
            **_build_forecast_point_fields(place, point),
            "days": rows,
            "model_run": format_swiss_time(run_time),
        }

    async def read_rain(
        self, location: str, start_moment: datetime, end_moment: datetime, nearby_location: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Read when and how much it may rain, and the weather in words, in 3-hour blocks from start to end.

        Times are timezone aware full hours. Raise CannotAnswerError when they are not, or when
        the forecast does not cover them. A last block that runs past the end of the forecast is
        left out.
        """
        _check_full_hour(start_moment)
        _check_full_hour(end_moment)
        _check_period_order(start_moment, end_moment)

        place, point = await self._find_place_and_point(location, nearby_location)
        chance_series, median_series, upper_series, pictogram_series = await asyncio.gather(
            self._read_series(parameters.PRECIPITATION_PROBABILITY, point),
            self._read_series(parameters.PRECIPITATION_3H, point),
            self._read_series(parameters.PRECIPITATION_Q90, point),
            self._read_series(parameters.WEATHER_PICTOGRAM, point),
        )

        # UTC, because adding hours in Swiss time goes wrong when the clocks change
        first_stamp = start_moment.astimezone(timezone.utc)
        last_stamp = end_moment.astimezone(timezone.utc)
        # The 3-hour values sit on the end of each block; the last block may end after the period,
        # as MeteoSwiss gives rain totals only per 3 hours
        block_ends = _create_stamps(first_stamp + RAIN_BLOCK, last_stamp, RAIN_BLOCK)
        # A period up to the end of the forecast is answered up to its last full block, as a last
        # block that runs past the period can also run past the forecast
        last_block_end = block_ends[-1]
        last_block_hours = _create_stamps(last_block_end - RAIN_BLOCK + HOUR, last_block_end, HOUR)
        last_block_is_published = (
            last_block_end in chance_series.values
            and last_block_end in median_series.values
            and last_block_end in pictogram_series.values
            and all(stamp in upper_series.values for stamp in last_block_hours)
        )
        if len(block_ends) > 1 and last_block_end > last_stamp and not last_block_is_published:
            block_ends.pop()
        # The heaviest hour needs the hourly values of every hour in the blocks
        hour_stamps = _create_stamps(first_stamp + HOUR, block_ends[-1], HOUR)

        for series, stamps in (
            (chance_series, block_ends),
            (median_series, block_ends),
            (pictogram_series, block_ends),
            (upper_series, hour_stamps),
        ):
            period_is_covered = all(stamp in series.values for stamp in stamps)
            if not period_is_covered:
                raise CannotAnswerError(
                    f"{format_swiss_time(start_moment)} to {format_swiss_time(end_moment)} is not fully covered "
                    f"by the forecast for {point.display_name}. {_describe_covered_range(median_series)}"
                )

        blocks = []
        for block_end in block_ends:
            block_start = block_end - RAIN_BLOCK
            block_hour_stamps = _create_stamps(block_start + HOUR, block_end, HOUR)
            heaviest_hour_mm = max(upper_series.values[stamp] for stamp in block_hour_stamps)
            weather, weather_emoji = _describe_pictogram(int(pictogram_series.values[block_end]))
            blocks.append({
                "from": format_swiss_time(block_start),
                "to": format_swiss_time(block_end),
                "rain_chance_percent": chance_series.values[block_end],
                "rainfall_median_mm": round(median_series.values[block_end], 1),
                "heaviest_hour_up_to_mm": round(heaviest_hour_mm, 1),
                "weather": weather,
                "weather_emoji": weather_emoji,
            })

        return {
            **_build_forecast_point_fields(place, point),
            "blocks": blocks,
            "model_run": format_swiss_time(median_series.run_time),
        }

    async def read_temperature(
        self, location: str, start_moment: datetime, end_moment: Optional[datetime] = None,
        nearby_location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Read the temperature hour by hour from start to end, or for the one hour from start when end is None.

        Times are timezone aware full hours. Raise CannotAnswerError when they are not, or when the
        forecast does not cover them.
        """
        _check_full_hour(start_moment)
        # A row covers the hour before its stamp, so the first hour is the row stamped one hour after start.
        # UTC, because adding hours in Swiss time goes wrong when the clocks change.
        first_stamp = start_moment.astimezone(timezone.utc) + HOUR

        if end_moment is None:
            last_stamp = first_stamp
            period = format_swiss_time(start_moment)
        else:
            _check_full_hour(end_moment)
            _check_period_order(start_moment, end_moment)
            last_stamp = end_moment.astimezone(timezone.utc)
            period = f"{format_swiss_time(start_moment)} to {format_swiss_time(end_moment)}"

        place, point = await self._find_place_and_point(location, nearby_location)
        temperature_series, lower_series, upper_series = await asyncio.gather(
            self._read_series(parameters.TEMPERATURE, point),
            self._read_series(parameters.TEMPERATURE_Q10, point),
            self._read_series(parameters.TEMPERATURE_Q90, point),
        )

        hour_stamps = _create_stamps(first_stamp, last_stamp, HOUR)
        for series in (temperature_series, lower_series, upper_series):
            period_is_covered = all(stamp in series.values for stamp in hour_stamps)
            if not period_is_covered:
                raise CannotAnswerError(
                    f"{period} is not fully covered by the forecast for {point.display_name}. "
                    f"{_describe_covered_range(temperature_series)}"
                )

        hours = []
        for stamp in hour_stamps:
            hours.append({
                "from": format_swiss_time(stamp - HOUR),
                "to": format_swiss_time(stamp),
                "temperature_c": temperature_series.values[stamp],
                "temperature_10th_percentile_c": lower_series.values[stamp],
                "temperature_90th_percentile_c": upper_series.values[stamp],
            })

        return {
            **_build_forecast_point_fields(place, point),
            "hours": hours,
            "model_run": format_swiss_time(temperature_series.run_time),
        }

    async def read_sunshine(
        self, location: str, start_moment: datetime, end_moment: Optional[datetime] = None,
        nearby_location: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add up the sunshine over a period in hours, or over the one hour from start when end is None."""
        _check_full_hour(start_moment)
        if end_moment is None:
            end_moment = start_moment.astimezone(timezone.utc) + HOUR
        _check_full_hour(end_moment)
        _check_period_order(start_moment, end_moment)
        # A row covers the hour before its stamp, so the first hour is the row stamped one hour after start.
        # UTC, because adding hours in Swiss time goes wrong when the clocks change.
        first_stamp = start_moment.astimezone(timezone.utc) + HOUR
        last_stamp = end_moment.astimezone(timezone.utc)
        hour_stamps = _create_stamps(first_stamp, last_stamp, HOUR)

        place, point = await self._find_place_and_point(location, nearby_location)
        series = await self._read_series(parameters.SUNSHINE, point)
        period_is_covered = all(stamp in series.values for stamp in hour_stamps)
        if not period_is_covered:
            raise CannotAnswerError(
                f"{format_swiss_time(start_moment)} to {format_swiss_time(end_moment)} is not fully covered "
                f"by the forecast for {point.display_name}. {_describe_covered_range(series)}"
            )

        sunshine_minutes = 0.0
        for stamp in hour_stamps:
            sunshine_minutes += series.values[stamp]
        return {
            **_build_forecast_point_fields(place, point),
            "value": round(sunshine_minutes / 60, 1),
            "unit": "h",
            "from": format_swiss_time(start_moment),
            "to": format_swiss_time(end_moment),
            "model_run": format_swiss_time(series.run_time),
        }

    async def read_wind(self, location: str, moment: datetime, nearby_location: Optional[str] = None) -> Dict[str, Any]:
        """Read the wind for the hour up to a moment: mean speed, gusts and direction."""
        _check_full_hour(moment)
        place, point = await self._find_place_and_point(location, nearby_location)
        speed_series, gust_series, upper_gust_series, direction_series = await asyncio.gather(
            self._read_series(parameters.WIND_SPEED, point),
            self._read_series(parameters.WIND_GUSTS, point),
            self._read_series(parameters.WIND_GUSTS_Q90, point),
            self._read_series(parameters.WIND_DIRECTION, point),
        )

        speed_kmh = _get_hourly_value(speed_series, moment, point)
        gusts_kmh = _get_hourly_value(gust_series, moment, point)
        upper_gusts_kmh = _get_hourly_value(upper_gust_series, moment, point)
        direction_degrees = _get_hourly_value(direction_series, moment, point)
        compass_point = find_compass_point(direction_degrees)
        return {
            **_build_forecast_point_fields(place, point),
            "speed_kmh": speed_kmh,
            "gusts_kmh": gusts_kmh,
            "gusts_90th_percentile_kmh": upper_gusts_kmh,
            "direction_degrees": direction_degrees,
            "compass_point": compass_point,
            "valid_at": format_swiss_time(moment),
            "model_run": format_swiss_time(speed_series.run_time),
        }

    async def read_cloud_cover(
        self, location: str, moment: datetime, nearby_location: Optional[str] = None
    ) -> Dict[str, Any]:
        """Estimate the total cloud cover at a moment from the three overlapping layers."""
        _check_full_hour(moment)
        place, point = await self._find_place_and_point(location, nearby_location)
        low_series, medium_series, high_series = await asyncio.gather(
            self._read_series(parameters.CLOUD_COVER_LOW, point),
            self._read_series(parameters.CLOUD_COVER_MEDIUM, point),
            self._read_series(parameters.CLOUD_COVER_HIGH, point),
        )

        low_fraction = _get_hourly_value(low_series, moment, point)
        medium_fraction = _get_hourly_value(medium_series, moment, point)
        high_fraction = _get_hourly_value(high_series, moment, point)

        # The layers overlap, so they cannot simply be added. Assuming they are independent, the sky
        # is clear only where all three are clear, which is the standard random overlap estimate.
        clear_sky = (1 - low_fraction) * (1 - medium_fraction) * (1 - high_fraction)
        return {
            **_build_forecast_point_fields(place, point),
            "value": round((1 - clear_sky) * 100, 1),
            "unit": "%",
            "low_percent": round(low_fraction * 100, 1),
            "medium_percent": round(medium_fraction * 100, 1),
            "high_percent": round(high_fraction * 100, 1),
            "valid_at": format_swiss_time(moment),
            "model_run": format_swiss_time(low_series.run_time),
        }

    async def read_freezing_level(
        self, location: str, moment: datetime, nearby_location: Optional[str] = None
    ) -> Dict[str, Any]:
        """Read the height of the 0 °C line at a moment."""
        _check_full_hour(moment)
        place, point = await self._find_place_and_point(location, nearby_location)
        series = await self._read_series(parameters.FREEZING_LEVEL, point)
        freezing_level_m = _get_hourly_value(series, moment, point)
        return {
            **_build_forecast_point_fields(place, point),
            "value": freezing_level_m,
            "unit": "m above sea level",
            "valid_at": format_swiss_time(moment),
            "model_run": format_swiss_time(series.run_time),
        }
