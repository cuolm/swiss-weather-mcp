"""An MCP server with Swiss weather forecasts and measurements, avalanche bulletins, and sun and moon times."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("swiss-weather-mcp")
except PackageNotFoundError:
    __version__ = "unknown"


__all__ = ["__version__"]
