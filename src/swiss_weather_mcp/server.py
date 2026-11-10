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
from .measurements.meteoswiss_source import MeteoSwissMeasurementSource
from .measurements.slf_source import SLFMeasurementSource
from .astronomy.service import AstronomyService
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
        self,
        forecast_service: ForecastService,
        measurement_service: MeasurementService,
        astronomy_service: AstronomyService,
        log_level: LogLevel,
    ) -> None:
        self.forecast_service = forecast_service
        self.measurement_service = measurement_service
        self.astronomy_service = astronomy_service
        self.mcp = MCPServer(
            name="swiss_weather_mcp_server",
            instructions=(
                "Weather for places in Switzerland from MeteoSwiss: what stations measure now and measured on past "
                "days (measurements_*), "
                "forecasts for today and the next 8 days (forecast_*), and when the Sun and the Moon rise "
                "and set (astronomy_*).\n"
                "- Times are Swiss local time in ISO 8601 without offset, such as \"2026-09-23T14:00\".\n"
                "- When a tool result has data_from, include it in your answer.\n"
                "- If a name matches several places, choose from what the user said, or ask which village it "
                "lies near. Then call again with the same location and that village as near."
            ),
            log_level=log_level,  # forwarded to uvicorn, which configures its own loggers
        )
        self._register_tools()

    def _register_tools(self) -> None:
        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_daily(location: str, start_date: str, days: int = 1, near: Near = None) -> dict:
            """
            Get the whole-day forecast at a place, for one day or several days in a row.

            Use this for "how is the weather tomorrow" or "how is the weather at the weekend", and
            for the total rain of a day. Each day covers a Swiss calendar day, 00:00 to 24:00 local
            time. The minimum and maximum are the lowest and highest hourly mean temperature of
            that day, and the weather words describe the daytime.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                start_date (str): The first day, e.g. "2026-09-23". Today or up to 8 days ahead.
                days (int): How many days in a row, from 1 to 9. Default 1.

            Returns:
                dict: data_from when the forecast point is far away or at a significantly different
                    height, the place, the model run and per day: date, weekday, lowest and highest
                    temperature, rain median with its 10th and 90th percentile, weather symbol code,
                    weather in words and emoji. In 8 of 10 possible outcomes, the day's rain lies between
                    its 10th and 90th percentile. A value is None when MeteoSwiss does not publish it for
                    the place.

            Examples:
                forecast_daily("Zürich", "2026-09-23")
                forecast_daily("Zürich", "2026-09-26", 2)   # a weekend
            """
            first_day = _parse_swiss_time(start_date).astimezone(SWISS_TZ).date()
            return await self.forecast_service.read_daily(location, first_day, days, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_rain(location: str, start: str, end: str, near: Near = None) -> dict:
            """
            Get when and how much it may rain at a place, and the weather in words, in 3-hour blocks
            over a period.

            Use this for "will it rain at 15:00" or "how is the weather this afternoon". Each block
            covers the 3 hours between its from and to times
            and gives the chance of rain, the median rainfall, how much the wettest hour may bring
            (its 90th percentile) and the weather in words. The median is often 0 when showers are
            possible, so read the chance and the "up to" amount as well. A period up to the end of
            the forecast ends with its last full 3-hour block. Medians do not add up, so for the
            total rain of a whole day use forecast_daily.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                start (str): Start of the period, a full hour, e.g. "2026-09-23T12:00". Today or up to 8 days ahead.
                end (str): End of the period, a full hour after start, e.g. "2026-09-23T18:00".

            Returns:
                dict: data_from when the forecast point is far away or at a significantly different
                    height, the place, the model run and per 3-hour block: from, to, chance of rain,
                    median rainfall, wettest hour up to, weather in words and emoji.

            Examples:
                forecast_rain("Zürich", "2026-09-23T15:00", "2026-09-23T18:00")    # 15:00
                forecast_rain("Zürich", "2026-09-23T12:00", "2026-09-23T18:00")    # the afternoon
            """
            start_moment = _parse_swiss_time(start)
            end_moment = _parse_swiss_time(end)
            return await self.forecast_service.read_rain(location, start_moment, end_moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_temperature(location: str, start: str, end: Optional[str] = None, near: Near = None) -> dict:
            """
            Get the temperature hour by hour at a place, with its likely range.

            Use this for "how warm is it at 15:00" or "how warm is it this afternoon". Without end
            it returns the one hour starting at start. Each row gives the mean temperature of one
            hour, between its from and to times.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                start (str): Start of the period, a full hour, e.g. "2026-09-23T15:00". Today or up to 8 days ahead.
                end (str): Optional. End of the period, a full hour after start, e.g. "2026-09-23T18:00".

            Returns:
                dict: data_from when the forecast point is far away or at a significantly different
                    height, the place, the model run and per hour: from, to, temperature with its 10th
                    and 90th percentile. In 8 of 10 possible outcomes, the temperature lies between its
                    10th and 90th percentile.

            Examples:
                forecast_temperature("Zürich", "2026-09-23T15:00")                        # one hour
                forecast_temperature("Zürich", "2026-09-23T12:00", "2026-09-23T18:00")    # the afternoon
            """
            start_moment = _parse_swiss_time(start)
            end_moment = _parse_swiss_time(end) if end is not None else None
            return await self.forecast_service.read_temperature(location, start_moment, end_moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_sunshine(location: str, start: str, end: Optional[str] = None, near: Near = None) -> dict:
            """
            Get the sunshine hours at a place over a period.

            Use this for "how sunny is it at 15:00" or "how sunny is it this afternoon". Without end
            it returns the one hour starting at start.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                start (str): Start of the period, a full hour, e.g. "2026-09-23T06:00". Today or up to 8 days ahead.
                end (str): Optional. End of the period, a full hour after start, e.g. "2026-09-23T18:00".

            Returns:
                dict: data_from when the forecast point is far away or at a significantly different
                    height, the place, the period, the model run and the sunshine hours.

            Examples:
                forecast_sunshine("Zürich", "2026-09-23T15:00")                        # one hour
                forecast_sunshine("Zürich", "2026-09-23T12:00", "2026-09-23T18:00")    # the afternoon
            """
            start_moment = _parse_swiss_time(start)
            end_moment = _parse_swiss_time(end) if end is not None else None
            return await self.forecast_service.read_sunshine(location, start_moment, end_moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_wind(location: str, when: str, near: Near = None) -> dict:
            """
            Get the wind at a place at a specific time: mean speed, strongest gust and direction.

            Use this for "how windy is it at 14:00". The speed is the mean over the hour up to that
            time, and the gust the strongest one-second gust in that hour. In 9 of 10 possible
            outcomes the gust stays below its 90th percentile, so the percentile is a cautious high
            estimate, not the highest gust possible. The direction is where the wind blows from, in
            degrees clockwise from north (0 is a north wind, 180 a south wind) and as a compass
            point.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                when (str): A full hour, e.g. "2026-09-23T14:00". Today or up to 8 days ahead.

            Returns:
                dict: data_from when the forecast point is far away or at a significantly different
                    height, the place, the valid time, the model run, mean speed, gust with its 90th
                    percentile, direction in degrees and as compass point.

            Examples:
                forecast_wind("Zürich", "2026-09-23T14:00")
            """
            moment = _parse_swiss_time(when)
            return await self.forecast_service.read_wind(location, moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_cloud_cover(location: str, when: str, near: Near = None) -> dict:
            """
            Get how cloudy it is at a place at a specific time.

            Use this for "how cloudy is it at 14:00". MeteoSwiss publishes low, medium and high
            cloud separately, and they overlap, so the total is an estimate that assumes the layers
            are independent. All three layers are returned as well, so low cloud such as fog can be
            told apart from high cloud.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                when (str): A full hour, e.g. "2026-09-23T14:00". Today or up to 8 days ahead.

            Returns:
                dict: data_from when the forecast point is far away or at a significantly different
                    height, the place, the valid time, the model run and the total cloud cover with its
                    low, medium and high layers.

            Examples:
                forecast_cloud_cover("Zürich", "2026-09-23T14:00")
            """
            moment = _parse_swiss_time(when)
            return await self.forecast_service.read_cloud_cover(location, moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def forecast_freezing_level(location: str, when: str, near: Near = None) -> dict:
            """
            Get the freezing level, the height of the 0 °C line, at a place at a specific time.

            Use this for "how high is the freezing level at 14:00".

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                when (str): A full hour, e.g. "2026-09-23T14:00". Today or up to 8 days ahead.

            Returns:
                dict: data_from when the forecast point is far away or at a significantly different
                    height, the place, the valid time, the model run and the freezing level above sea
                    level.

            Examples:
                forecast_freezing_level("Zürich", "2026-09-23T14:00")
            """
            moment = _parse_swiss_time(when)
            return await self.forecast_service.read_freezing_level(location, moment, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def measurements_current(location: str, near: Near = None) -> dict:
            """
            Get the latest measurements of a MeteoSwiss station near a place.

            Use this for "how warm is it now" or "is it raining now". The values are those of the
            station named in data_from. MeteoSwiss publishes new values about every 10 minutes.
            When a station of the SLF, the WSL Institute for Snow and Avalanche Research, is closer
            to the place in distance and height, mostly in the mountains, slf_station adds its
            temperature, humidity and wind over the last 30 minutes, with its own data_from.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".

            Returns:
                dict: data_from, the place, the time of the measurements, temperature, humidity, dew
                    point and sea-level pressure; wind speed, gust and direction of the last 10 minutes;
                    rain and sunshine of the last 10 minutes and the last hour; strongest gust of the last
                    hour; temperature and pressure change over 3 hours. A value is None when the station
                    has not published it. slf_station, only when an SLF station is closer: its data_from,
                    time, temperature, humidity, wind speed, direction and gust over 30 minutes.

            Examples:
                measurements_current("Zürich")
            """
            return await self.measurement_service.read_current(location, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def measurements_history(location: str, start_date: str, days: int = 1, near: Near = None) -> dict:
            """
            Get what a MeteoSwiss station near a place measured on past days, for one day or several days in a row.

            Use this for "how warm was it yesterday" or "how much did it rain last week". The values are
            the daily values of the station named in data_from. Each day covers a UTC day, 00:00 to 24:00
            UTC, which is 01:00 to 01:00 Swiss time in winter and 02:00 to 02:00 in summer. MeteoSwiss
            publishes a day's values the next morning. The days go back to when the station started, at
            some stations to the 1860s. For the warmest day of a year, ask for all its days.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                start_date (str): The first day, e.g. "2026-09-23". Yesterday or earlier.
                days (int): How many days in a row, from 1 to 366, so up to a whole year. Default 1.

            Returns:
                dict: data_from, the place and per day: date, weekday, mean, lowest and highest
                    temperature, rain, sunshine hours and strongest gust. A value is None when the
                    station did not measure it.

            Examples:
                measurements_history("Zürich", "2026-09-22")
                measurements_history("Zürich", "2026-09-14", 7)     # a week
                measurements_history("Zürich", "2025-01-01", 365)   # a whole year
                measurements_history("Zürich", "1990-02-27")
            """
            first_day = _parse_swiss_time(start_date).astimezone(SWISS_TZ).date()
            return await self.measurement_service.read_history(location, first_day, days, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def measurements_snow(location: str, near: Near = None) -> dict:
            """
            Get the snow depth and the new snow near a place, from MeteoSwiss and SLF stations.

            Use this for "how much snow is there in Davos" or "how much new snow fell in Zermatt".
            Snow depth and new snow can come from two different stations, each named in its own
            data_from. The snow depth comes from a MeteoSwiss station, an automatic SLF station or
            an SLF observer; the new snow only from SLF, the WSL Institute for Snow and Avalanche
            Research. An observer measures the new snow by hand each morning, from November to
            April; at automatic stations, SLF calculates it from the snow depth. Only stations close
            enough in distance and height are used, so in the lowlands there is often no new snow.
            Always say which station each value comes from, how far away it is and how much higher
            or lower.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Davos Dorf".

            Returns:
                dict: the place, snow_depth and new_snow. snow_depth: data_from, time and snow depth.
                    new_snow: data_from, time, new snow of the last 24 hours and of the last 3 days.
                    A block is null when no station close enough has a current value.

            Examples:
                measurements_snow("Davos Dorf")
            """
            return await self.measurement_service.read_snow(location, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def measurements_extremes(extreme: ExtremeName) -> dict:
            """
            Get where in Switzerland it is warmest, coldest, windiest, wettest or sunniest now.

            Use this for "where is it warmest now" or "where is it sunny now". The values are those
            of about 160 MeteoSwiss stations, so give the altitude with each station. For the
            sunniest, the stations are grouped by their sun in the last 10 minutes with MeteoSwiss's
            sunshine terms: mostly sunny (8 to 10 minutes), rather sunny (5 to 7), partly sunny (2 to
            4), only a little sunshine (1) and hardly any sunshine (0). The cantons come in order of
            their stations' average sun, sunniest first. A station is rainy when it had rain in the
            last 10 minutes.

            Args:
                extreme (str): One of "warmest", "coldest", "windiest", "wettest" or "sunniest".

            Returns:
                dict: the time of the measurements and the first 5 stations, each with altitude, canton
                    and value. For the sunniest instead the number of stations in each sunshine group, in
                    Switzerland and in each canton, the sunniest canton first. For the wettest also the
                    number of rainy stations out of those that measure rain, in Switzerland and in each
                    canton.

            Examples:
                measurements_extremes("warmest")
            """
            return await self.measurement_service.read_extremes(extreme)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def astronomy_sun(location: str, date: str, near: Near = None) -> dict:
            """
            Get when the Sun rises and sets at a place on a day, and when civil twilight begins and ends.

            Use this for "when does the sun rise tomorrow" or "until when is it light tonight". Civil
            twilight is the light before sunrise and after sunset; outside it, artificial light is
            normally needed outdoors. The times are for a level horizon, so in a valley the Sun
            appears later behind the mountains.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                date (str): The day, e.g. "2026-09-23".

            Returns:
                dict: the place, the date, civil twilight begin, sunrise, sunset and civil twilight end.

            Examples:
                astronomy_sun("Zürich", "2026-09-23")
            """
            day = _parse_swiss_time(date).astimezone(SWISS_TZ).date()
            return await self.astronomy_service.read_sun(location, day, near)

        @self.mcp.tool(annotations=READS_OPEN_DATA)
        @_handle_tool_call
        async def astronomy_moon(location: str, date: str, near: Near = None) -> dict:
            """
            Get when the Moon rises and sets at a place on a day, its phase and how much of it is lit.

            Use this for "when does the moon rise tonight" or "how full is the moon tonight". The
            times are for a level horizon, so in a valley the Moon appears later behind the
            mountains. The day has a moon_phase_time when New Moon, First Quarter, Full Moon or Last
            Quarter happens on it. The lit percent is the one at 12:00.

            Args:
                location (str): Name of a place in Switzerland, e.g. "Zürich".
                date (str): The day, e.g. "2026-09-23".

            Returns:
                dict: the place, the date, moonrise and moonset (None when the Moon does not rise or
                    set that day), phase with emoji, phase time when a main phase falls on the day, and lit
                    percent.

            Examples:
                astronomy_moon("Zürich", "2026-09-23")
            """
            day = _parse_swiss_time(date).astimezone(SWISS_TZ).date()
            return await self.astronomy_service.read_moon(location, day, near)

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
    meteoswiss_source = MeteoSwissMeasurementSource(CACHE_DIR)
    slf_source = SLFMeasurementSource(CACHE_DIR)
    measurement_service = MeasurementService(place_finder, meteoswiss_source, slf_source)
    astronomy_service = AstronomyService(place_finder)
    return SwissWeatherMCPServer(forecast_service, measurement_service, astronomy_service, args.log_level)


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
