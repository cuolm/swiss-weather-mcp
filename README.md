# Swiss Weather MCP Server
![Logo](docs/swissweathermcp-logo.png)
[![PyPI](https://img.shields.io/pypi/v/swiss-weather-mcp.svg)](https://pypi.org/project/swiss-weather-mcp/)
[![License](https://img.shields.io/github/license/cuolm/swiss-weather-mcp.svg)](/LICENSE.txt)
[![Release](https://github.com/cuolm/swiss-weather-mcp/actions/workflows/release.yaml/badge.svg)](https://github.com/cuolm/swiss-weather-mcp/actions/workflows/release.yaml)
[![Tests](https://github.com/cuolm/swiss-weather-mcp/actions/workflows/tests.yaml/badge.svg)](https://github.com/cuolm/swiss-weather-mcp/actions/workflows/tests.yaml)
<!-- mcp-name: io.github.cuolm/swiss-weather-mcp -->

An **[MCP](https://modelcontextprotocol.io/)** server that gives AI applications weather information for
places across Switzerland:

- **Forecasts** for today and the next 8 days: temperature, rain, sunshine, wind, clouds and freezing level
- **Measurements** from about 160 MeteoSwiss and 200 SLF mountain stations: now, on past days, and where
  in Switzerland it is warmest, coldest, windiest, wettest or sunniest
- **Snow and avalanches**: snow depth, new snow and the SLF avalanche bulletin
- **Sun and Moon**: rise and set times, civil twilight and the Moon's phase

Not an official product of MeteoSwiss, SLF or swisstopo. See [Data Sources](#data-sources).

Answers pass through an AI model and can be wrong; use them at your own risk, without liability.
Before going into the mountains, check the official
[avalanche bulletin](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/) and the
[MeteoSwiss forecast](https://www.meteoswiss.admin.ch).

## Quick Start
Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then add this to your AI app's MCP config file:
```json
{
  "mcpServers": {
    "swiss-weather": {
      "command": "uvx",
      "args": ["swiss-weather-mcp@latest"]
    }
  }
}
```
`uvx` downloads the server on first use, and `@latest` makes it check for a new release on each start.
If your app cannot find `uvx`, replace `uvx` with the full path that `which uvx` prints.
If the file already has content, add only the `"swiss-weather"` entry inside its `"mcpServers"`.

### In Claude Desktop and Claude Code
Instead of the config file, install the plugin. First make sure [uv](https://docs.astral.sh/uv/getting-started/installation/) is installed.

**Claude Desktop:** go to Customize > Plugins > Add > Add marketplace, enter `cuolm/claude-plugins` and add
Swiss Weather. Or download the
[desktop extension](https://github.com/cuolm/swiss-weather-mcp/releases/latest/download/swiss-weather-mcp.mcpb),
double-click it and select Install.

**Claude Code:**
```bash
claude plugin marketplace add cuolm/claude-plugins
claude plugin install swiss-weather-mcp@cuolm
```

## Installation
Install the server once with uv, instead of starting it with `uvx` on each start as in [Quick Start](#quick-start):
```bash
uv tool install swiss-weather-mcp
```

### Over stdio
Your AI app starts the server itself. Add this to its MCP config file:
```json
{
  "mcpServers": {
    "swiss-weather": {
      "command": "swiss-weather-mcp"
    }
  }
}
```
If your app cannot find `swiss-weather-mcp`, use the full path that `which swiss-weather-mcp` prints.

### Over Streamable HTTP
The server runs on its own, and your AI app connects to it by URL. Start it with:
```bash
swiss-weather-mcp --transport=streamable-http --host=localhost --port=8050
```
Then add its URL to your AI app's MCP config file:
```json
{
  "mcpServers": {
    "swiss-weather": {
      "url": "http://localhost:8050/mcp/"
    }
  }
}
```
To use another port, change `8050` in `--port` and in the URL. `--help` lists all flags. Where the server keeps its files, and `--cache-all-forecast-points`: see [Cache and Downloads](docs/how-it-works.md#cache-and-downloads).

### With Docker
The image runs the server over streamable HTTP and needs no uv:
```bash
docker run -p 127.0.0.1:8050:8050 ghcr.io/cuolm/swiss-weather-mcp:latest
```
Then add its URL to your AI app's MCP config file:
```json
{
  "mcpServers": {
    "swiss-weather": {
      "url": "http://localhost:8050/mcp/"
    }
  }
}
```
To use another port, change the first `8050` in `-p` and the one in the URL.

The Docker images are built and published automatically by GitHub Actions to the project's [GitHub Container Registry](https://ghcr.io/cuolm/swiss-weather-mcp), tagged `:latest` (newest release) and by version.
Each `docker run` starts a new container with an empty cache. Mount a volume to keep it, for example
`-v swiss-weather-cache:/home/app/.cache/swiss-weather-mcp`.

## Available Tools
Times are Swiss local time (see [Times](docs/how-it-works.md#times)). Every tool with a `location` also takes an
optional `near`, needed only when several places share the name (see [Locations](docs/how-it-works.md#locations)).
Answers name the forecast point when it is not next to the place (see [Forecast Points](docs/how-it-works.md#forecast-points)).

| Tool | Answer |
|------|--------|
| `forecast_daily(location, start_date, days)` | 1 to 9 days: lowest and highest temperature, rain, weather with an emoji |
| `forecast_rain(location, start, end)` | Chance and amount of rain, and the weather, in 3-hour blocks |
| `forecast_temperature(location, start, end)` | Temperature and its likely range, hour by hour or for one hour |
| `forecast_sunshine(location, start, end)` | Hours of sunshine over a period, or in one hour |
| `forecast_wind(location, when)` | Mean speed, gusts and direction |
| `forecast_cloud_cover(location, when)` | Total cloud cover and its low, medium and high layers |
| `forecast_freezing_level(location, when)` | Height of the 0 °C line |
| `measurements_current(location)` | What a station near the place measures now (see [Measurements](docs/how-it-works.md#measurements)) |
| `measurements_history(location, start_date, days, values)` | 1 to 366 past days: lowest, mean and highest temperature, rain, sunshine, strongest gust, snow depth and new snow |
| `measurements_snow(location)` | Snow depth now, and new snow of the last 24 hours and 3 days (see [Measurements](docs/how-it-works.md#measurements)) |
| `measurements_extremes(extreme)` | Where it is `warmest`, `coldest`, `windiest`, `wettest` or `sunniest` now, or where the snow is deepest (`deepest_snow`) or most new snow fell (`most_new_snow`) |
| `avalanche_bulletin(location)` | SLF avalanche bulletin valid now: danger level, avalanche problems and texts (see [Avalanche Bulletin](docs/how-it-works.md#avalanche-bulletin)) |
| `astronomy_sun(location, date)` | Sunrise, sunset and civil twilight |
| `astronomy_moon(location, date)` | Moonrise, moonset, phase and how much is lit |
| `current_date_and_time()` | Today's weekday and the Swiss time now |

## Details
- [How It Works](docs/how-it-works.md): times, places, forecast points, each tool in detail, forecast
  medians, cache and downloads
- [Development](docs/development.md): running from source, the test client and the project structure

## Data Sources
- [Local forecasts](https://opendatadocs.meteoswiss.ch/e-forecast-data/e4-local-forecast-data) and [SwissMetNet measurements](https://opendatadocs.meteoswiss.ch/a-data-groundbased/a1-automatic-weather-stations): [MeteoSwiss](https://www.meteoswiss.admin.ch) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Source: MeteoSwiss
- IMIS mountain stations, snow measured by observers and the [avalanche bulletin](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/): [SLF](https://www.slf.ch) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Source: WSL Institute for Snow and Avalanche Research SLF. Report your observations with the [SLF feedback tool](https://pro.slf.ch/reply/public/#/)
- Places from [swissNAMES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissnames3d), municipalities and cantons from [swissBOUNDARIES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d): [swisstopo](https://www.swisstopo.admin.ch) under its [terms of use](https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices). Source: Federal Office of Topography swisstopo

The server does not collect or share personal data. It only downloads the public data listed above
from MeteoSwiss, SLF and swisstopo, and writes its logs only where it runs.

## Support
Questions and bugs: [GitHub Issues](https://github.com/cuolm/swiss-weather-mcp/issues).

## License
Licensed under the [Apache License 2.0](/LICENSE.txt).
