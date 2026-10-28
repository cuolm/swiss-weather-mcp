"""Print what the astronomy tools answer for a place today, such as: uv run scripts/print_astronomy.py Zürich"""
import asyncio
import json
import sys
from datetime import datetime

from swiss_weather_mcp.astronomy.service import AstronomyService
from swiss_weather_mcp.formatting import SWISS_TZ
from swiss_weather_mcp.places import PlaceFinder
from swiss_weather_mcp.server import CACHE_DIR

DEFAULT_LOCATION = "Zürich"


async def print_astronomy(location: str) -> None:
    """Call each astronomy service method for today and print its answer."""
    service = AstronomyService(PlaceFinder(CACHE_DIR))
    today = datetime.now(SWISS_TZ).date()

    answers = {
        "astronomy_moon": await service.read_moon(location, today),
        "astronomy_sun": await service.read_sun(location, today),
    }
    for tool_name, answer in answers.items():
        print(f'=== {tool_name}("{location}", "{today.isoformat()}")')
        print(json.dumps(answer, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        location = sys.argv[1]
    else:
        location = DEFAULT_LOCATION
    asyncio.run(print_astronomy(location))
