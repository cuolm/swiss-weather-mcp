"""Print what each measurement tool answers now, such as: uv run scripts/print_measurements.py Zürich"""
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from swiss_weather_mcp.measurements.extremes import EXTREME_NAME_TO_EXTREME
from swiss_weather_mcp.measurements.service import MeasurementService
from swiss_weather_mcp.measurements.meteoswiss_source import MeteoSwissMeasurementSource
from swiss_weather_mcp.measurements.slf_source import SLFMeasurementSource
from swiss_weather_mcp.places import PlaceFinder
from swiss_weather_mcp.server import CACHE_DIR

DEFAULT_LOCATION = "Zürich"


def print_answer(tool_call: str, answer: dict) -> None:
    """Print one tool call and its answer as indented JSON."""
    print(f"=== {tool_call}")
    print(json.dumps(answer, indent=2, ensure_ascii=False))


async def print_measurements(location: str) -> None:
    """Call each measurement service method and print its answer."""
    service = MeasurementService(
        PlaceFinder(CACHE_DIR), MeteoSwissMeasurementSource(CACHE_DIR), SLFMeasurementSource(CACHE_DIR)
    )

    current_answer = await service.read_current(location)
    print_answer(f'measurements_current("{location}")', current_answer)

    # The days are UTC days, and the last one published is yesterday's
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    history_answer = await service.read_history(location, yesterday, 1)
    print_answer(f'measurements_history("{location}", "{yesterday.isoformat()}")', history_answer)

    for extreme in EXTREME_NAME_TO_EXTREME:
        extremes_answer = await service.read_extremes(extreme)
        print_answer(f'measurements_extremes("{extreme}")', extremes_answer)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        location = sys.argv[1]
    else:
        location = DEFAULT_LOCATION
    asyncio.run(print_measurements(location))
