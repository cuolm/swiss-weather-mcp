import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import parameters
from ..formatting import find_compass_point, format_swiss_time
from ..locations import ForecastPoint, LocationFinder
from .source import ForecastSeries, LocalForecastSource

logger = logging.getLogger(__name__)

# MeteoSwiss publishes today and the next 8 days
MAX_DAYS = 9

HOUR = timedelta(hours=1)
RAIN_BLOCK = timedelta(hours=3)
MAX_RAIN_OUTLOOK = timedelta(hours=48)
MAX_HOURLY_FORECAST = timedelta(hours=24)


def _check_full_hour(moment: datetime) -> None:
    """Refuse a time that is not a full hour, as MeteoSwiss forecasts only whole hours."""
    if moment.minute or moment.second or moment.microsecond:
        full_hour = moment.replace(minute=0, second=0, microsecond=0)
        raise ValueError(
            f"{format_swiss_time(moment)} is not a full hour. MeteoSwiss forecasts whole hours, so use a "
            f"full hour such as {format_swiss_time(full_hour)} or {format_swiss_time(full_hour + HOUR)}."
        )


def _list_stamps(first_stamp: datetime, last_stamp: datetime, step: timedelta) -> List[datetime]:
    """Return stamps one step apart, from the first until one reaches the last."""
    stamps = [first_stamp]
    while stamps[-1] < last_stamp:
        stamps.append(stamps[-1] + step)
    return stamps


def _check_period_order(start_moment: datetime, end_moment: datetime) -> None:
    """Refuse a period whose end is not after its start."""
    if end_moment <= start_moment:
        raise ValueError(
            f"The end of the period, {format_swiss_time(end_moment)}, must be after its start, "
            f"{format_swiss_time(start_moment)}."
        )


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


def _read_day_value(series: Optional[ForecastSeries], day: date) -> Optional[float]:
    """Return the value of a daily series for one day, or None when the series or the day is missing."""
    if series is None:
        return None
    # A daily row is stamped 00:00 on the Swiss calendar day it describes
    day_stamp = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return series.values.get(day_stamp)


def _find_run_time(all_series: Tuple[Optional[ForecastSeries], ...]) -> Optional[datetime]:
    """Return the model run of the first published series, or None when none is published."""
    for series in all_series:
        if series is not None:
            return series.run_time
    return None


class ForecastService:
    """Answer weather questions for a location: the value, its unit, the resolved point and the model run."""

    def __init__(self, location_finder: LocationFinder, forecast_source: LocalForecastSource):
        self.location_finder = location_finder
        self.forecast_source = forecast_source

    async def _find_point(self, location: str) -> ForecastPoint:
        """Find the forecast point for a location in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.location_finder.find_point, location)

    async def _read_series(self, parameter: str, point: ForecastPoint) -> ForecastSeries:
        """Read one parameter for one point in a worker thread, so a download does not block other requests."""
        return await asyncio.to_thread(self.forecast_source.read_series, parameter, point)

    def _read_value_at(self, series: ForecastSeries, moment: datetime, point: ForecastPoint) -> float:
        """Return the value stamped at a full hour: an average or sum of the hour up to it, or a snapshot."""
        if moment not in series.values:
            raise ValueError(
                f"{format_swiss_time(moment)} is outside the forecast for {point.display_name}. "
                f"{_describe_covered_range(series)}"
            )
        return series.values[moment]

    def _sum_between(self, series: ForecastSeries, start_moment: datetime, end_moment: datetime, point: ForecastPoint) -> float:
        """Add up the hourly values from start to end."""
        _check_period_order(start_moment, end_moment)
        # A row covers the hour before its stamp, so the first hour is the row stamped one hour after start.
        # UTC, because adding hours in Swiss time goes wrong when the clocks change.
        first_stamp = start_moment.astimezone(timezone.utc) + HOUR
        last_stamp = end_moment.astimezone(timezone.utc)
        hour_stamps = _list_stamps(first_stamp, last_stamp, HOUR)

        period_is_covered = all(stamp in series.values for stamp in hour_stamps)
        if not period_is_covered:
            raise ValueError(
                f"{format_swiss_time(start_moment)} to {format_swiss_time(end_moment)} is not fully covered "
                f"by the forecast for {point.display_name}. {_describe_covered_range(series)}"
            )

        total = 0.0
        for stamp in hour_stamps:
            total += series.values[stamp]
        return total

    async def read_freezing_level(self, location: str, moment: datetime) -> Dict[str, Any]:
        """Read the height of the 0 °C line at a moment."""
        _check_full_hour(moment)
        point = await self._find_point(location)
        series = await self._read_series(parameters.FREEZING_LEVEL, point)
        freezing_level_m = self._read_value_at(series, moment, point)
        return {
            "value": freezing_level_m,
            "unit": "m above sea level",
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "valid_at": format_swiss_time(moment),
            "model_run": format_swiss_time(series.run_time),
        }

    async def read_wind(self, location: str, moment: datetime) -> Dict[str, Any]:
        """
        Read the mean wind speed, the strongest gust with its 90th percentile and the wind direction
        for the hour up to a moment.
        """
        _check_full_hour(moment)
        point = await self._find_point(location)
        speed_series, gust_series, upper_gust_series, direction_series = await asyncio.gather(
            self._read_series(parameters.WIND_SPEED, point),
            self._read_series(parameters.WIND_GUSTS, point),
            self._read_series(parameters.WIND_GUSTS_Q90, point),
            self._read_series(parameters.WIND_DIRECTION, point),
        )

        speed_kmh = self._read_value_at(speed_series, moment, point)
        gusts_kmh = self._read_value_at(gust_series, moment, point)
        upper_gusts_kmh = self._read_value_at(upper_gust_series, moment, point)
        direction_degrees = self._read_value_at(direction_series, moment, point)
        compass_point = find_compass_point(direction_degrees)
        return {
            "speed_kmh": speed_kmh,
            "gusts_kmh": gusts_kmh,
            "gusts_90th_percentile_kmh": upper_gusts_kmh,
            "direction_degrees": direction_degrees,
            "compass_point": compass_point,
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "valid_at": format_swiss_time(moment),
            "model_run": format_swiss_time(speed_series.run_time),
        }

    async def read_sunshine_hours(self, location: str, start_moment: datetime, end_moment: datetime) -> Dict[str, Any]:
        """Add up the sunshine over a period, in hours."""
        _check_full_hour(start_moment)
        _check_full_hour(end_moment)
        point = await self._find_point(location)
        series = await self._read_series(parameters.SUNSHINE, point)
        sunshine_minutes = self._sum_between(series, start_moment, end_moment, point)
        return {
            "value": round(sunshine_minutes / 60, 1),
            "unit": "h",
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "from": format_swiss_time(start_moment),
            "to": format_swiss_time(end_moment),
            "model_run": format_swiss_time(series.run_time),
        }

    async def read_total_cloud_cover(self, location: str, moment: datetime) -> Dict[str, Any]:
        """Estimate the total cloud cover at a moment from the three overlapping layers."""
        _check_full_hour(moment)
        point = await self._find_point(location)
        low_series, medium_series, high_series = await asyncio.gather(
            self._read_series(parameters.CLOUD_COVER_LOW, point),
            self._read_series(parameters.CLOUD_COVER_MEDIUM, point),
            self._read_series(parameters.CLOUD_COVER_HIGH, point),
        )

        low_fraction = self._read_value_at(low_series, moment, point)
        medium_fraction = self._read_value_at(medium_series, moment, point)
        high_fraction = self._read_value_at(high_series, moment, point)

        # The layers overlap, so they cannot simply be added. Assuming they are independent, the sky
        # is clear only where all three are clear, which is the standard random overlap estimate.
        clear_sky = (1 - low_fraction) * (1 - medium_fraction) * (1 - high_fraction)
        return {
            "value": round((1 - clear_sky) * 100, 1),
            "unit": "%",
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "valid_at": format_swiss_time(moment),
            "low_percent": round(low_fraction * 100, 1),
            "medium_percent": round(medium_fraction * 100, 1),
            "high_percent": round(high_fraction * 100, 1),
            "model_run": format_swiss_time(low_series.run_time),
        }

    async def read_hourly_forecast(
        self, location: str, start_moment: datetime, end_moment: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Read the temperature with its 10th and 90th percentile, rain chance and weather for every
        hour from start to end.

        Parameters:
            location (str): Location name or postal code.
            start_moment (datetime): Start of the period, timezone aware.
            end_moment (Optional[datetime]): End of the period, at most MAX_HOURLY_FORECAST after the
                start; None for the one hour starting at start.

        Returns:
            Dict[str, Any]: The resolved point, one row per hour labelled with its start and end, and
                the model run.
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
            if end_moment - start_moment > MAX_HOURLY_FORECAST:
                max_hours = int(MAX_HOURLY_FORECAST.total_seconds() // 3600)
                raise ValueError(f"An hourly forecast covers at most {max_hours} hours; for whole days use daily_forecast.")
            last_stamp = end_moment.astimezone(timezone.utc)
            period = f"{format_swiss_time(start_moment)} to {format_swiss_time(end_moment)}"

        point = await self._find_point(location)
        temperature_series, lower_series, upper_series, chance_series, pictogram_series = await asyncio.gather(
            self._read_series(parameters.TEMPERATURE, point),
            self._read_series(parameters.TEMPERATURE_Q10, point),
            self._read_series(parameters.TEMPERATURE_Q90, point),
            self._read_series(parameters.PRECIPITATION_PROBABILITY, point),
            self._read_series(parameters.WEATHER_PICTOGRAM, point),
        )

        hour_stamps = _list_stamps(first_stamp, last_stamp, HOUR)
        for series in (temperature_series, lower_series, upper_series, chance_series, pictogram_series):
            period_is_covered = all(stamp in series.values for stamp in hour_stamps)
            if not period_is_covered:
                raise ValueError(
                    f"{period} is not fully covered by the forecast for {point.display_name}. "
                    f"{_describe_covered_range(pictogram_series)}"
                )

        hours = []
        for stamp in hour_stamps:
            weather, weather_emoji = _describe_pictogram(int(pictogram_series.values[stamp]))
            hours.append({
                "from": format_swiss_time(stamp - HOUR),
                "to": format_swiss_time(stamp),
                "temperature_c": temperature_series.values[stamp],
                "temperature_10th_percentile_c": lower_series.values[stamp],
                "temperature_90th_percentile_c": upper_series.values[stamp],
                "rain_chance_percent": chance_series.values[stamp],
                "weather": weather,
                "weather_emoji": weather_emoji,
            })

        return {
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "hours": hours,
            "model_run": format_swiss_time(temperature_series.run_time),
        }

    async def read_rain_outlook(self, location: str, start_moment: datetime, end_moment: datetime) -> Dict[str, Any]:
        """
        Read when and how much it may rain, in 3-hour blocks from start to end.

        Parameters:
            location (str): Location name or postal code.
            start_moment (datetime): Start of the period, timezone aware.
            end_moment (datetime): End of the period, at most MAX_RAIN_OUTLOOK after the start.

        Returns:
            Dict[str, Any]: The resolved point, one row per block with the rain chance, the median
                rainfall and the heaviest hour's 90th percentile, and the model run.
        """
        _check_full_hour(start_moment)
        _check_full_hour(end_moment)
        _check_period_order(start_moment, end_moment)
        if end_moment - start_moment > MAX_RAIN_OUTLOOK:
            max_hours = int(MAX_RAIN_OUTLOOK.total_seconds() // 3600)
            raise ValueError(f"A rain outlook covers at most {max_hours} hours; ask for a shorter period.")

        point = await self._find_point(location)
        chance_series, median_series, upper_series = await asyncio.gather(
            self._read_series(parameters.PRECIPITATION_PROBABILITY, point),
            self._read_series(parameters.PRECIPITATION_3H, point),
            self._read_series(parameters.PRECIPITATION_Q90, point),
        )

        # UTC, because adding hours in Swiss time goes wrong when the clocks change
        first_stamp = start_moment.astimezone(timezone.utc)
        last_stamp = end_moment.astimezone(timezone.utc)
        # The 3-hour values sit on the end of each block; the last block may end after the period,
        # as MeteoSwiss gives rain totals only per 3 hours
        block_ends = _list_stamps(first_stamp + RAIN_BLOCK, last_stamp, RAIN_BLOCK)
        # The heaviest hour needs the hourly values of every hour in the blocks
        hour_stamps = _list_stamps(first_stamp + HOUR, block_ends[-1], HOUR)

        for series, stamps in (
            (chance_series, block_ends),
            (median_series, block_ends),
            (upper_series, hour_stamps),
        ):
            period_is_covered = all(stamp in series.values for stamp in stamps)
            if not period_is_covered:
                raise ValueError(
                    f"{format_swiss_time(start_moment)} to {format_swiss_time(end_moment)} is not fully covered "
                    f"by the forecast for {point.display_name}. {_describe_covered_range(median_series)}"
                )

        blocks = []
        for block_end in block_ends:
            block_start = block_end - RAIN_BLOCK
            block_hour_stamps = _list_stamps(block_start + HOUR, block_end, HOUR)
            heaviest_hour_mm = max(upper_series.values[stamp] for stamp in block_hour_stamps)
            blocks.append({
                "from": format_swiss_time(block_start),
                "to": format_swiss_time(block_end),
                "rain_chance_percent": chance_series.values[block_end],
                "rainfall_median_mm": round(median_series.values[block_end], 1),
                "heaviest_hour_up_to_mm": round(heaviest_hour_mm, 1),
            })

        return {
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "blocks": blocks,
            "model_run": format_swiss_time(median_series.run_time),
        }

    async def _read_series_if_published(self, parameter: str, point: ForecastPoint) -> Optional[ForecastSeries]:
        """Read one parameter for one point, or return None when MeteoSwiss does not publish it there."""
        try:
            return await self._read_series(parameter, point)
        except ValueError as error:
            logger.info(f"No daily {parameter} for {point.display_name}: {error}")
            return None

    async def read_daily_forecast(self, location: str, first_day: date, days: int) -> Dict[str, Any]:
        """
        Read the whole-day forecast for one or several days in a row, one row per day.

        Parameters:
            location (str): Location name or postal code.
            first_day (date): The first Swiss calendar day.
            days (int): How many days, from 1 to MAX_DAYS.

        Returns:
            Dict[str, Any]: The resolved point, one row per day with published values (date,
                weekday, minimum and maximum temperature, rainfall median and 10th and 90th
                percentile, weather in words with its emoji), and the model run. Fields MeteoSwiss
                does not publish for this location are None.
        """
        if not 1 <= days <= MAX_DAYS:
            raise ValueError(f"days must be between 1 and {MAX_DAYS}, not {days}.")

        point = await self._find_point(location)
        min_series, max_series, rain_series, rain_lower_series, rain_upper_series, pictogram_series = await asyncio.gather(
            self._read_series_if_published(parameters.TEMPERATURE_DAY_MIN, point),
            self._read_series_if_published(parameters.TEMPERATURE_DAY_MAX, point),
            self._read_series_if_published(parameters.PRECIPITATION_DAY, point),
            self._read_series_if_published(parameters.PRECIPITATION_DAY_Q10, point),
            self._read_series_if_published(parameters.PRECIPITATION_DAY_Q90, point),
            self._read_series_if_published(parameters.WEATHER_PICTOGRAM_DAY, point),
        )

        rows = []
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            temperature_min_c = _read_day_value(min_series, day)
            temperature_max_c = _read_day_value(max_series, day)
            rainfall_median_mm = _read_day_value(rain_series, day)
            rainfall_lower_mm = _read_day_value(rain_lower_series, day)
            rainfall_upper_mm = _read_day_value(rain_upper_series, day)
            pictogram_value = _read_day_value(pictogram_series, day)
            day_values = (
                temperature_min_c, temperature_max_c, rainfall_median_mm, rainfall_lower_mm, rainfall_upper_mm,
                pictogram_value,
            )
            if all(value is None for value in day_values):
                continue

            row: Dict[str, Any] = {
                "date": day.isoformat(),
                "weekday": f"{day:%A}",
                "temperature_min_c": temperature_min_c,
                "temperature_max_c": temperature_max_c,
                "rainfall_median_mm": rainfall_median_mm,
                "rainfall_10th_percentile_mm": rainfall_lower_mm,
                "rainfall_90th_percentile_mm": rainfall_upper_mm,
                "weather": None,
            }
            # The pictogram is published as a code, which is only useful once it is spelled out
            if pictogram_value is not None:
                row["pictogram_code"] = int(pictogram_value)
                row["weather"], row["weather_emoji"] = _describe_pictogram(row["pictogram_code"])
            rows.append(row)

        all_series = (min_series, max_series, rain_series, rain_lower_series, rain_upper_series, pictogram_series)
        run_time = _find_run_time(all_series)
        if not rows or run_time is None:
            raise ValueError(f"MeteoSwiss has no daily forecast for {point.display_name} from {first_day.isoformat()}.")
        return {
            "location": point.display_name,
            "altitude_m": point.altitude_m,
            "days": rows,
            "model_run": format_swiss_time(run_time),
        }
