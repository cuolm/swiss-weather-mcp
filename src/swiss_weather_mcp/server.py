import argparse
import functools
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable, Dict, Optional

import requests
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from platformdirs import user_cache_path
from pydantic import Field

from .log import LOG_LEVELS, LogLevel, setup_logging
from .errors import CannotAnswerError
from .forecast.service import ForecastService
from .forecast.source import ForecastSource
from .places import PlaceFinder
from .measurements.service import ExtremeName, MeasurementService
from .measurements.source import MeasurementSource
from .formatting import SWISS_TZ

logger = logging.getLogger(__name__)

# Shared, OS-standard cache location (survives across working directories the server may be launched from).
# Override with SWISS_WEATHER_MCP_CACHE_DIR, e.g. to isolate cache location in tests or Docker.
CACHE_DIR = Path(os.environ.get("SWISS_WEATHER_MCP_CACHE_DIR", user_cache_path("swiss-weather-mcp")))

# The optional near argument of every tool that takes a location. The example keeps a small model
# from putting the village into location, or swapping the two, which it did without it.
Near = Annotated[Optional[str], Field(description=(
    "Only when several places share the name: a village next to the place the user means. "
    "For \"the Blausee near Bettmeralp\" use location \"Blausee\" and near \"Bettmeralp\". Leave it out otherwise."
))]

# The Claude apps run read-only tools without asking the user each time. Every tool only
# reads, and all but the clock read the Swiss open data portal.
READS_OPEN_DATA = ToolAnnotations(read_only_hint=True, open_world_hint=True)
READS_THE_CLOCK = ToolAnnotations(read_only_hint=True, open_world_hint=False)

Tool = Callable[..., Awaitable[Dict[str, Any]]]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport layer for MCP server (default: stdio)",
    )
    parser.add_argument("--host", default="localhost", help="Server host (used only for HTTP)")
    parser.add_argument("--port", type=int, default=8050, help="Server port (used only for HTTP)")
    parser.add_argument(
        "--cache-all-forecast-points",
        action="store_true",
        help="Keep the whole published file (about 31 MB per parameter) instead of only the rows "
             "for the requested forecast point, so questions about further places need no download",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=LOG_LEVELS,
        help="Logging level for the server (default: INFO)",
    )
    return parser.parse_args()


def _parse_swiss_time(timestamp: str) -> datetime:
    """
    Read an ISO 8601 timestamp or date, such as "2026-09-23T14:00" or "2026-09-23".

    Without an offset it is Swiss local time; an explicit offset is kept. Raise CannotAnswerError
    when the text is not a timestamp.
    """
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise CannotAnswerError(
            f"'{timestamp}' is not a valid timestamp, use for example '2026-09-23T14:00' or '2026-09-23'"
        ) from error
    return moment if moment.tzinfo else moment.replace(tzinfo=SWISS_TZ)


def _handle_tool_call(tool: Tool) -> Tool:
    """
    Log each tool call, and turn the failures the model can act on into a ToolError.

    mcp shows a ToolError's message to the model. Any other error stays a crash:
    mcp hides it from the model and logs its traceback.
    """
    @functools.wraps(tool)
    async def run_tool(**arguments: Any) -> Dict[str, Any]:
        try:
            answer = await tool(**arguments)
        except CannotAnswerError as error:
            logger.warning(f"{tool.__name__}: {error}")
            raise ToolError(str(error)) from error
        except requests.RequestException as error:
            message = f"Could not download the data, try again later: {error}"
            logger.warning(f"{tool.__name__}: {message}")
            raise ToolError(message) from error

        logger.info(f"{tool.__name__}: {arguments} -> {answer}")
        return answer

    return run_tool


class SwissWeatherMCPServer:
    """The MCP server: registers the weather tools and runs them over stdio or streamable HTTP."""

    def __init__(
        self, forecast_service: ForecastService, measurement_service: MeasurementService, log_level: LogLevel
    ) -> None:
        self.forecast_service = forecast_service
        self.measurement_service = measurement_service
        self.mcp = MCPServer(
            name="swiss_weather_mcp_server",
            instructions=(
                "Weather for places in Switzerland from MeteoSwiss: what stations measure now (measurements_*), "
                "and forecasts for today and the next 8 days (forecast_*).\n"
                "- Values for a place come from the MeteoSwiss station or forecast point named in data_from, "
                "which can be kilometres away and higher or lower. When an answer has data_from, say which "
                "one in the answer.\n"
                "- Forecast values are medians, and medians do not add up: hours with 0 mm can still make a "
                "wet day. Take the rain of a whole day from forecast_daily.\n"
                "- If a name matches several places, choose from what the user said, or ask which village it "
                "lies near. Then call again with the same location and that village as near."
            ),
            log_level=log_level,  # forwarded to uvicorn, which configures its own loggers
        )
        self._register_tools()

    def _register_tools(self) -> None:
        @self.mcp.tool(annotations=READS_THE_CLOCK)
        def current_date_and_time() -> str:
            """
            Get the current weekday and Swiss local time.

            Call this first when the question is relative, such as "tomorrow" or "tonight", because
            the forecast tools take a real date rather than an offset. The time is written the way
            the tools read it, ISO 8601 without an offset, so it can be sent back to them as it is.

            Returns:
                str: For example "Today is Wednesday, 2026-09-23T13:02 (Swiss time)".
            """
            now = datetime.now(SWISS_TZ)
            return f"Today is {now:%A}, {now:%Y-%m-%dT%H:%M} (Swiss time)"

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_daily(location: str, start_date: str, days: int = 1, near: Near = None) -> dict:
            """
            Get the whole-day forecast for a place, for one day or several days in a row.

            Use this for "how is the weather" on a day, at the weekend or during the week, and for
            the total rain of a day. Each day covers a Swiss calendar day, 00:00 to 24:00 local
            time. The minimum and maximum are the lowest and highest hourly mean temperature of
            that day, and the weather words describe the daytime.

            Args:
                location (str): Place name: a town, village, city district, lake or peak (e.g. "Zurich").
                start_date (str): The first Swiss calendar day in ISO 8601, e.g. "2026-09-23". Today or up to 8 days ahead.
                days (int): How many days in a row, from 1 to 9. Default 1.

            Returns:
                dict: The place, one row per day and the model run; the field names give the units.
                    In 8 of 10 possible outcomes, the day's rain lies between its 10th and 90th
                    percentile. A value is None when MeteoSwiss does not publish it for the place.

            Examples:
                forecast_daily("Zurich", "2026-09-23")
                forecast_daily("Lugano", "2026-09-26", 2)   # a weekend
            """
            first_day = _parse_swiss_time(start_date).astimezone(SWISS_TZ).date()
            return await self.forecast_service.read_daily_forecast(location, first_day, days, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_temperature(location: str, start: str, end: Optional[str] = None, near: Near = None) -> dict:
            """
            Get the temperature hour by hour at a place, with its likely range.

            Use this for one hour, such as "how warm is it at 15:00", or part of a day, such as "how
            warm is the afternoon". Without end it returns the one hour starting at start. Each row
            is one hour, from its from time to its to time, with the mean temperature of that hour.
            For whole days or periods longer than a day use forecast_daily, which answers in far
            fewer rows, and for rain and the weather in words forecast_rain.

            Args:
                location (str): Place name: a town, village, city district, lake or peak (e.g. "Zurich").
                start (str): Swiss local time as a full hour in ISO 8601 without offset, e.g. "2026-09-23T15:00". Today or up to 8 days ahead.
                end (str): Optional. Swiss local time as a full hour in ISO 8601 without offset, after start.

            Returns:
                dict: The place, one row per hour and the model run; the field names give the units.
                    In 8 of 10 possible outcomes, the temperature lies between its 10th and 90th
                    percentile.

            Examples:
                forecast_temperature("Zurich", "2026-09-23T15:00")                        # one hour
                forecast_temperature("Zurich", "2026-09-23T12:00", "2026-09-23T18:00")    # the afternoon
            """
            start_moment = _parse_swiss_time(start)
            end_moment = _parse_swiss_time(end) if end is not None else None
            return await self.forecast_service.read_temperature_forecast(location, start_moment, end_moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_rain(location: str, start: str, end: str, near: Near = None) -> dict:
            """
            Get when and how much it may rain at a place, and the weather in words, in 3-hour blocks
            over a period.

            Use this for "will it rain this afternoon", "do I need an umbrella", "when does the rain
            stop" or "how is the weather this afternoon". Each block gives the chance of rain, the
            median rainfall of the 3 hours, how much the wettest hour of the block may bring (its
            90th percentile), and the weather of the 3 hours in words. The median is often 0 when
            showers are possible, so read the chance and the "up to" amount as well.
            A period up to the end of the forecast ends with its last full 3-hour block. For the
            total rain of a whole day use forecast_daily.

            Args:
                location (str): Place name: a town, village, city district, lake or peak (e.g. "Zurich").
                start (str): Swiss local time the period starts, as a full hour in ISO 8601 without offset, e.g. "2026-09-23T12:00". Today or up to 8 days ahead.
                end (str): Swiss local time the period ends, as a full hour in ISO 8601 without offset, after start.

            Returns:
                dict: The place, one row per 3-hour block and the model run; the field names give
                    the units.

            Examples:
                forecast_rain("Zurich", "2026-09-23T12:00", "2026-09-24T00:00")
                forecast_rain("Lugano", "2026-09-24T06:00", "2026-09-24T18:00")
            """
            start_moment = _parse_swiss_time(start)
            end_moment = _parse_swiss_time(end)
            return await self.forecast_service.read_rain_outlook(location, start_moment, end_moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_sunshine(location: str, start: str, end: str, near: Near = None) -> dict:
            """
            Get the sunshine hours for a place over a period.

            MeteoSwiss publishes sunshine as minutes per hour, which are added up over the period
            and reported as hours.

            Args:
                location (str): Place name: a town, village, city district, lake or peak (e.g. "Zurich").
                start (str): Swiss local time the period starts, as a full hour in ISO 8601 without offset, e.g. "2026-09-23T06:00". Today or up to 8 days ahead.
                end (str): Swiss local time the period ends, as a full hour in ISO 8601 without offset, e.g. "2026-09-23T18:00".

            Returns:
                dict: The sunshine in hours, the place, the period and the model run.

            Examples:
                forecast_sunshine("Zurich", "2026-09-23T00:00", "2026-09-24T00:00")   # the whole day
                forecast_sunshine("Zurich", "2026-09-23T12:00", "2026-09-23T18:00")   # the afternoon
            """
            start_moment = _parse_swiss_time(start)
            end_moment = _parse_swiss_time(end)
            return await self.forecast_service.read_sunshine_hours(location, start_moment, end_moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_wind(location: str, when: str, near: Near = None) -> dict:
            """
            Get the wind at a place at a specific time: mean speed, strongest gust and direction.

            The speed is the mean over the hour up to that time, and the gust the strongest
            one-second gust in that hour. In 9 of 10 possible outcomes the gust stays below its
            90th percentile, so the percentile is a cautious high estimate, not the highest gust
            possible. The direction is where the wind blows from, in degrees clockwise from north
            (0 is a north wind, 180 a south wind) and as a compass point.

            Args:
                location (str): Place name: a town, village, city district, lake or peak (e.g. "Zurich").
                when (str): Swiss local time as a full hour in ISO 8601 without offset, e.g. "2026-09-23T14:00". Today or up to 8 days ahead.

            Returns:
                dict: The wind, the place, the time it is valid for and the model run; the field
                    names give the units.

            Examples:
                forecast_wind("Zurich", "2026-09-23T14:00")
                forecast_wind("Titlis", "2026-09-24T12:00")
            """
            moment = _parse_swiss_time(when)
            return await self.forecast_service.read_wind(location, moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_cloud_cover(location: str, when: str, near: Near = None) -> dict:
            """
            Get how cloudy it is at a place at a specific time.

            MeteoSwiss publishes low, medium and high cloud separately, and they overlap, so the
            total is an estimate that assumes the layers are independent. All three layers are
            returned as well, which tells low fog apart from thin high cloud.

            When a number is not needed, the weather words of forecast_rain answer "how cloudy".

            Args:
                location (str): Place name: a town, village, city district, lake or peak (e.g. "Zurich").
                when (str): Swiss local time as a full hour in ISO 8601 without offset, e.g. "2026-09-23T14:00". Today or up to 8 days ahead.

            Returns:
                dict: The total cloud cover and its low, medium and high layers in percent, the
                    place, the time it is valid for and the model run.

            Examples:
                forecast_cloud_cover("Zurich", "2026-09-23T14:00")
                forecast_cloud_cover("Locarno", "2026-09-24T09:00")
            """
            moment = _parse_swiss_time(when)
            return await self.forecast_service.read_total_cloud_cover(location, moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_freezing_level(location: str, when: str, near: Near = None) -> dict:
            """
            Get the height of the 0 degree line at a place at a specific time.

            This is the height where the air is at 0 degrees. Falling snow usually reaches somewhat
            below it, so it tells whether rain or snow falls at a height. It does not tell whether
            old snow lies on the ground: this server has no snow depth data.

            Args:
                location (str): Place name: a town, village, city district, lake or peak (e.g. "Zurich").
                when (str): Swiss local time as a full hour in ISO 8601 without offset, e.g. "2026-09-23T14:00". Today or up to 8 days ahead.

            Returns:
                dict: The freezing level in metres above sea level, the place, the time it is
                    valid for and the model run.

            Examples:
                forecast_freezing_level("Zermatt", "2026-09-23T14:00")
                forecast_freezing_level("Arosa", "2026-09-25T06:00")
            """
            moment = _parse_swiss_time(when)
            return await self.forecast_service.read_freezing_level(location, moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def measurements_current(location: str, near: Near = None) -> dict:
            """
            Get the latest measurements of a MeteoSwiss station near the location.

            These are the measurements of that station, not of the location itself. The station is
            chosen by distance and height, so it can be several kilometres away and hundreds of
            metres higher or lower than the location. Always start the answer with the data_from
            sentence, which names the station, its distance and its height difference, and then
            give the values as the station's measurements. MeteoSwiss publishes new values about
            every 10 minutes.

            Temperature, humidity, dew point and pressure are measured at measured_at. Rain,
            sunshine, wind speed, the strongest gust and the wind direction cover the 10 minutes up
            to it. The temperature and pressure changes compare measured_at with 3 hours before it,
            negative when the value fell. The rain and sunshine of the last hour are the totals of
            its six 10-minute values, and the strongest gust of the last hour is the highest of them.
            For later today or the coming days use the forecast_* tools.

            Args:
                location (str): Place name: a town, village, city district, lake or peak (e.g. "Zurich").

            Returns:
                dict: data_from, which names the station with its distance and height difference,
                    the place, the time of the measurements and the measured values; the field names
                    give the units. A value is None when the station does not measure it, and a
                    change or a last-hour value is None when the station has not published all the
                    measurements it needs, such as shortly after midnight UTC.

            Examples:
                measurements_current("Zurich")
                measurements_current("Braunwald")
            """
            return await self.measurement_service.read_current_conditions(location, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def measurements_extremes(extreme: ExtremeName) -> dict:
            """
            Get where in Switzerland it is warmest, coldest, windiest, wettest or sunniest now.

            This compares the latest measurements of about 160 MeteoSwiss stations. They are the
            values of stations, not of towns, and not a forecast. A high mountain station is
            colder and windier than the valleys, so name the altitude with a station. MeteoSwiss
            publishes new values about every 10 minutes.

            The warmest, coldest and windiest stations are ranked by the temperature and by the
            strongest gust. For the sunniest and the wettest the answer counts stations: a station
            is sunny when the sun shone for at least 5 of the last 10 minutes, and rainy when it
            measured rain in the last 10 minutes. The counts are given for Switzerland and for
            each canton, as the number of sunny or rainy stations out of the stations that measure
            it. Always give both numbers, because a share from one or two stations says little
            about a canton. A canton with 0 has stations, but none of them is sunny or rainy. A
            canton that is not listed has no station that measures it. Liechtenstein is listed like
            a canton. At night no station is sunny.

            Args:
                extreme (str): One of "warmest", "coldest", "windiest", "wettest" or "sunniest".

            Returns:
                dict: The time of the measurements and the first 5 stations, or for the sunniest and
                    the wettest the counts in all and for each canton, the highest share first; the
                    field names give the units. The wettest also lists the first 5 rainy stations.
                    A station that does not measure the value is left out everywhere.

            Examples:
                measurements_extremes("warmest")
                measurements_extremes("sunniest")
            """
            return await self.measurement_service.read_current_extremes(extreme)

    def run(self, transport: str, host: str, port: int) -> None:
        """Serve the tools over stdio, or over streamable HTTP on host and port, until the process stops."""
        if transport == "stdio":
            logger.info("Running server with stdio transport")
            self.mcp.run(transport="stdio")
        else:
            logger.info("Running server with Streamable HTTP transport")
            self.mcp.run(transport="streamable-http", host=host, port=port, stateless_http=True)


def _build_server(args: argparse.Namespace) -> SwissWeatherMCPServer:
    """Build the sources and services from the command line options, and the server that uses them."""
    place_finder = PlaceFinder(CACHE_DIR)
    place_finder.start_loading_places()
    forecast_source = ForecastSource(CACHE_DIR, cache_all_forecast_points=args.cache_all_forecast_points)
    forecast_service = ForecastService(place_finder, forecast_source)
    measurement_source = MeasurementSource(CACHE_DIR)
    measurement_service = MeasurementService(place_finder, measurement_source)
    return SwissWeatherMCPServer(forecast_service, measurement_service, args.log_level)


def main():
    """Start the MCP server from the command line."""
    try:
        args = _parse_args()
        setup_logging(args.log_level)
        server = _build_server(args)
        server.run(args.transport, args.host, args.port)
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down.")
    except Exception:
        logger.exception("Fatal error in MCP server")
        sys.exit(1)


if __name__ == "__main__":
    main()
