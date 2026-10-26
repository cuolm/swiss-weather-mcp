"""Print what each forecast tool answers for a place tomorrow, such as: uv run scripts/print_forecasts.py Zürich"""
import asyncio
import json
import sys
from datetime import datetime, time, timedelta

from swiss_weather_mcp.formatting import SWISS_TZ
from swiss_weather_mcp.forecast.service import ForecastService
from swiss_weather_mcp.forecast.source import ForecastSource
from swiss_weather_mcp.places import PlaceFinder
from swiss_weather_mcp.server import CACHE_DIR

DEFAULT_LOCATION = "Zürich"


async def print_forecasts(location: str) -> None:
    """Call each forecast service method for tomorrow and print its answer."""
    service = ForecastService(PlaceFinder(CACHE_DIR), ForecastSource(CACHE_DIR))
    tomorrow = datetime.now(SWISS_TZ).date() + timedelta(days=1)
    noon = datetime.combine(tomorrow, time(12), SWISS_TZ)
    evening = datetime.combine(tomorrow, time(18), SWISS_TZ)

    answers = {
        "forecast_daily": await service.read_daily(location, tomorrow, 1),
        "forecast_rain": await service.read_rain(location, noon, evening),
        "forecast_temperature": await service.read_temperature(location, noon, evening),
        "forecast_sunshine": await service.read_sunshine(location, noon, evening),
        "forecast_wind": await service.read_wind(location, noon),
        "forecast_cloud_cover": await service.read_cloud_cover(location, noon),
        "forecast_freezing_level": await service.read_freezing_level(location, noon),
    }
    for tool_name, answer in answers.items():
        print(f"=== {tool_name}")
        print(json.dumps(answer, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        location = sys.argv[1]
    else:
        location = DEFAULT_LOCATION
    asyncio.run(print_forecasts(location))
