"""Print what each measurement tool answers now, such as: uv run scripts/print_measurements.py Zürich"""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Dict

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements.extremes import EXTREME_NAME_TO_EXTREME
from swiss_weather_mcp.measurements.service import MeasurementService
from swiss_weather_mcp.measurements.meteoswiss_source import MeteoSwissMeasurementSource
from swiss_weather_mcp.measurements.slf_source import SLFMeasurementSource
from swiss_weather_mcp.places import PlaceFinder
from swiss_weather_mcp.server import CACHE_DIR

DEFAULT_LOCATION = "Zürich"


async def print_answer(tool_call: str, reading: Awaitable[Dict[str, Any]]) -> None:
    """Print one tool call and its answer as indented JSON, or the message of a tool that cannot answer."""
    print(f"=== {tool_call}")
    try:
        answer = await reading
    except CannotAnswerError as error:
        print(error)
        return
    print(json.dumps(answer, indent=2, ensure_ascii=False))


async def print_measurements(location: str) -> None:
    """Call each measurement service method and print its answer."""
    service = MeasurementService(
        PlaceFinder(CACHE_DIR), MeteoSwissMeasurementSource(CACHE_DIR), SLFMeasurementSource(CACHE_DIR)
    )

    current_reading = service.read_current(location)
    await print_answer(f'measurements_current("{location}")', current_reading)

    # The days are UTC days, and the last one published is yesterday's
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    history_reading = service.read_history(location, yesterday, 1)
    await print_answer(f'measurements_history("{location}", "{yesterday.isoformat()}")', history_reading)

    snow_reading = service.read_snow(location)
    await print_answer(f'measurements_snow("{location}")', snow_reading)

    for extreme in EXTREME_NAME_TO_EXTREME:
        extremes_reading = service.read_extremes(extreme)
        await print_answer(f'measurements_extremes("{extreme}")', extremes_reading)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        location = sys.argv[1]
    else:
        location = DEFAULT_LOCATION
    asyncio.run(print_measurements(location))
