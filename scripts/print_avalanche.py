"""Print what the avalanche bulletin tool answers now, such as: uv run scripts/print_avalanche.py Braunwald"""
import asyncio
import json
import sys

from swiss_weather_mcp.avalanche.service import AvalancheService
from swiss_weather_mcp.avalanche.source import SLFBulletinSource
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.places import PlaceFinder
from swiss_weather_mcp.server import CACHE_DIR

DEFAULT_LOCATION = "Braunwald"


async def print_avalanche(location: str) -> None:
    """Call the avalanche service and print its answer, or the message of a tool that cannot answer."""
    service = AvalancheService(PlaceFinder(CACHE_DIR), SLFBulletinSource(CACHE_DIR))
    print(f'=== avalanche_bulletin("{location}")')
    try:
        answer = await service.read_bulletin(location)
    except CannotAnswerError as error:
        print(error)
        return
    print(json.dumps(answer, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        location = sys.argv[1]
    else:
        location = DEFAULT_LOCATION
    asyncio.run(print_avalanche(location))
