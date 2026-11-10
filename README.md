# Swiss Weather MCP Server
![Logo](docs/swissweathermcp-logo.png)
[![PyPI](https://img.shields.io/pypi/v/swiss-weather-mcp.svg)](https://pypi.org/project/swiss-weather-mcp/)
[![License](https://img.shields.io/github/license/cuolm/swiss-weather-mcp.svg)](/LICENSE.txt)
[![Release](https://github.com/cuolm/swiss-weather-mcp/actions/workflows/release.yaml/badge.svg)](https://github.com/cuolm/swiss-weather-mcp/actions/workflows/release.yaml)
[![Tests](https://github.com/cuolm/swiss-weather-mcp/actions/workflows/tests.yaml/badge.svg)](https://github.com/cuolm/swiss-weather-mcp/actions/workflows/tests.yaml)
<!-- mcp-name: io.github.cuolm/swiss-weather-mcp -->

A **Model Context Protocol ([MCP](https://modelcontextprotocol.io/))** server that provides AI applications with weather information for any place in Switzerland.

- Forecasts for today and the next 8 days from the [MeteoSwiss local forecasts](https://opendatadocs.meteoswiss.ch/e-forecast-data/e4-local-forecast-data), updated every hour
- Measurements from about 160 [SwissMetNet weather stations](https://opendatadocs.meteoswiss.ch/a-data-groundbased/a1-automatic-weather-stations), updated every 10 minutes
- Snow depth and new snow from MeteoSwiss and [SLF](https://www.slf.ch) stations and observers
- Places on the Swiss national map, such as towns, districts, lakes and peaks, from [swissNAMES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissnames3d)
- When the Sun and the Moon rise and set at a place, the civil twilight, the Moon's phase and how much of it is lit, calculated for any day
- A bundled MCP client for testing

> **Data sources.** This project is not an official MeteoSwiss product.
> - Weather forecasts and measurements: [MeteoSwiss Open Data](https://opendata.swiss/en/organization/bundesamt-fur-meteorologie-und-klimatologie-meteoschweiz). Source: MeteoSwiss
> - Measurements of the IMIS mountain stations and the snow measured by observers: [SLF](https://www.slf.ch), the WSL Institute for Snow and Avalanche Research, under [CC BY 4.0](https://www.slf.ch/en/services-and-products/slf-data-service/). Source: SLF
> - Place names: [swissNAMES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissnames3d). Source: Federal Office of Topography swisstopo
> - Municipalities and cantons: [swissBOUNDARIES3D](https://www.swisstopo.admin.ch/en/landscape-model-swissboundaries3d). Source: Federal Office of Topography swisstopo

## Table of Contents
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Usage](#usage)
- [Available Tools](#available-tools)
- [Good to Know](#good-to-know)
- [Example Usage with LMStudio](#example-usage-with-lmstudio)
- [Resources](#resources)
- [License](#license)

## Project Structure
```text
swiss-weather-mcp/
├── src/swiss_weather_mcp/
│   ├── server.py           # MCP server and its tools
│   ├── places/             # swisstopo place lookup
│   ├── forecast/           # MeteoSwiss local forecasts
│   ├── measurements/       # MeteoSwiss SwissMetNet and SLF IMIS measurements
│   ├── astronomy/          # Sunrise, sunset, moonrise, moonset and phase
│   ├── downloads.py        # Downloads and caches files from data.geo.admin.ch and SLF
│   ├── distance.py         # Distance plus height gap, to choose a station for a place
│   ├── coordinates.py      # WGS84 latitude and longitude to LV95 and back
│   ├── formatting.py       # Times, wind directions and height differences in the answers
│   ├── errors.py           # The error for questions the data cannot answer
│   ├── log.py              # Logging setup
│   └── client.py           # MCP client
├── tests/swiss_weather_mcp/  # Tests
└── .github/workflows/      # CI and release pipelines
```

## Installation
```bash
# As a command line tool
uv tool install swiss-weather-mcp

# With the optional MCP client, which pulls in the OpenAI SDK
uv tool install 'swiss-weather-mcp[client]'

# As a library dependency
uv add swiss-weather-mcp  # or: pip install swiss-weather-mcp
```

From source:
```bash
git clone https://github.com/cuolm/swiss-weather-mcp.git
cd swiss-weather-mcp
uv sync --extra client  # or, in a virtual environment: pip install -e ".[client]"
```
No configuration is needed, the server reads everything it requires from MeteoSwiss and swisstopo.

## Usage

### Running the Server
```bash
# stdio (default)
swiss-weather-mcp-server

# streamable-http
swiss-weather-mcp-server --transport=streamable-http --host=localhost --port=8050
```
From a source checkout, put `uv run` in front. `--help` lists all flags.

Where the server keeps its files, and `--cache-all-forecast-points`: see [Cache and Downloads](#cache-and-downloads).

### Running the Server with Docker
Images are built and published automatically by GitHub Actions to the project's [GitHub Container Registry](https://ghcr.io/cuolm/swiss-weather-mcp), tagged `:latest` (newest release) and by version.

```bash
# Published image
docker run -p 8050:8050 ghcr.io/cuolm/swiss-weather-mcp:latest

# Built from a source checkout
docker build -t swiss-weather-mcp .
docker run -p 8050:8050 swiss-weather-mcp
```
The server is then at `http://localhost:8050/mcp/`.

Each `docker run` starts a new container with an empty cache. Mount a volume to keep it, for example
`-v swiss-weather-cache:/root/.cache/swiss-weather-mcp`.

### Running the MCP Client
The bundled MCP client tests the server over stdio. It needs the `client` extra (see [Installation](#installation)) and starts the MCP server itself, but not the LLM, which has to be running first, for example in `llama-server`.

```bash
# Install llama.cpp, which provides llama-server, here with Homebrew on macOS or Linux
brew install llama.cpp

# Start llama-server with a model in its own terminal; it downloads the model on first use
llama-server --jinja --no-mmproj -hf unsloth/Qwen3.5-4B-GGUF --port 8080

# Run the client against it in a second terminal
swiss-weather-mcp-client --model=unsloth/Qwen3.5-4B-GGUF --base-url=http://localhost:8080/v1
```
From a source checkout, put `uv run --extra client` in front of the client. Other ways to install llama.cpp
are in its [installation guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/install.md).

`--jinja` uses the model's chat template, which tool calling needs (the default in current llama.cpp),
and `--no-mmproj` skips the vision projector. `--base-url` is the server's OpenAI compatible API, and
`--model` the name it lists under `/v1/models`.

## Available Tools
Times are Swiss local time (see [Times](#times)). Every tool with a `location` also takes an
optional `near`, needed only when several places share the name (see [Locations](#locations)).
Answers name the forecast point when it is not next to the place (see [Forecast Points](#forecast-points)).

| Tool | Answer |
|------|--------|
| `forecast_daily(location, start_date, days)` | 1 to 9 days: lowest and highest temperature, rain, weather with an emoji |
| `forecast_rain(location, start, end)` | Chance and amount of rain in 3-hour blocks |
| `forecast_temperature(location, start, end)` | Temperature hour by hour, or for one hour |
| `forecast_sunshine(location, start, end)` | Hours of sunshine over a period, or in one hour |
| `forecast_wind(location, when)` | Mean speed, gusts and direction |
| `forecast_cloud_cover(location, when)` | Total cloud cover and its low, medium and high layers |
| `forecast_freezing_level(location, when)` | Height of the 0 °C line |
| `measurements_current(location)` | What a station near the place measures now (see [Measurements](#measurements)) |
| `measurements_history(location, start_date, days)` | 1 to 366 past days: lowest, mean and highest temperature, rain, sunshine, strongest gust |
| `measurements_snow(location)` | Snow depth now, and new snow of the last 24 hours and 3 days (see [Measurements](#measurements)) |
| `measurements_extremes(extreme)` | Where it is `warmest`, `coldest`, `windiest`, `wettest` or `sunniest` now |
| `astronomy_sun(location, date)` | Sunrise, sunset and civil twilight |
| `astronomy_moon(location, date)` | Moonrise, moonset, phase and how much is lit |
| `current_date_and_time()` | Today's weekday and the Swiss time now |

## Good to Know

### Times
- Times are Swiss clock time in ISO 8601, such as `"2026-09-23T14:00"`. The server applies summer or
  winter time.
- Answers give times with their UTC offset, such as `2026-09-23T13:00+02:00`.
- An hourly value stamped `14:00` covers 13:00 to 14:00. Cloud cover and the freezing level are values
  at that moment.
- The forecast covers today and the next 8 days. `current_date_and_time()` gives today's date, which
  questions such as "tomorrow" or "tonight" need.

### Locations
- A location is a place name from swissNAMES3D: a town, village, city district, peak, pass, lake,
  glacier, valley or railway station, such as `Zürich`, `Oerlikon`, `Titlis` or `Zürichsee`.
- Case and accents don't matter, and a few English names such as `Geneva` work too.
- A name of several places, such as `Blausee`, needs `near`: a village next to the one the user means,
  such as `near="Riederalp"`. Without it, the tool lists all places of the name, the biggest first,
  each with the call that chooses it and the municipality and canton it lies in:
  ```
  'Blausee' is the name of 6 places. [...] The biggest first:
    location="Blausee", near="Blausee-Mitholz": district and lake, fewer than 20 people, 887 m, municipality Kandergrund, canton Bern
    location="Blausee", near="Ramiswil": settlement, fewer than 20 people, 683 m, municipality Mümliswil-Ramiswil, canton Solothurn
    ...
  ```
- The municipalities and cantons carry swisstopo's names, in the local language, such as `Genève`
  or `Graubünden`. They appear once the swissBOUNDARIES3D GeoPackage is downloaded in the
  background at start.
- An unknown name gets up to 10 suggestions, such as `Davos Dorf` and `Davos Platz` for `Davos`.

### Forecast Points
- The forecast comes from a MeteoSwiss forecast point near the place. An answer names it in
  data_from when it is more than 1 km away or more than 50 m higher or lower.
- Temperature changes faster with height than with distance: in the standard atmosphere (ISO 2533),
  air cools by 0.65 °C per 100 m.
- So among the points within 5 km, the server takes the one with the smallest distance plus 20 times
  the height gap: 100 m of height gap counts like 2 km of distance. Without a point within 5 km, it
  takes the nearest one.
- The factor 20 was tested on the real data: up to 20, the height gaps of lakes and peaks shrink a lot,
  and every town of 1,000 people or more still gets a point within 3 km.

### Measurements
- `measurements_current` uses one of the about 160 SwissMetNet stations: of the 3 with the smallest
  distance plus 20 times the height gap, the first that published a temperature in the last hour.
  Stations are far fewer than forecast points, so the station can still be far from the place and
  higher or lower.
- Every answer starts with a sentence that says so, such as "The measurements for Braunwald (1290 m)
  are those of the MeteoSwiss station Elm (958 m), 13.5 km away and 332 m lower."
- It gives the temperature, humidity, dew point, rain and sunshine in the last 10 minutes, wind, gust,
  direction and sea-level pressure, plus the temperature and pressure change over the last 3 hours and
  the rain, sunshine and strongest gust of the last hour.
- In the mountains it adds an `slf_station` block from one of the about 200 IMIS stations of the
  [SLF](https://www.slf.ch), most between 1300 and 3300 m: the temperature, humidity, wind, direction
  and strongest 5-second gust of the 30 minutes up to its latest row. It is added only when the station's
  distance plus 20 times the height gap is smaller than the MeteoSwiss station's, and the station
  published a temperature in the last hour (of the 3 best, the first that did). On the real data that is
  no place below 800 m and most places above 2000 m. Its own data_from names the station, its distance
  and height. When SLF cannot be reached, the answer comes without the block.
- `measurements_history` gives MeteoSwiss's daily values of the station chosen the same way: of the 3
  first stations, the first with a temperature on the days asked. A day runs from 00:00 to 24:00 UTC,
  which is 01:00 to 01:00 Swiss time in winter and 02:00 to 02:00 in summer, and MeteoSwiss publishes
  it the next morning. The days go back to when the station started, such as 1864 for Zürich /
  Fluntern, where the rain from 0 to 0 UTC and the gust start in 1981; a value the station did not
  measure is None. Days before this year come from a file of up to about 6.4 MB per station.
- `measurements_snow` gives the snow depth and the new snow, each from its own station: of the 3 with
  the smallest distance plus 20 times the height gap, the first with a current value. Each has its own
  data_from, which names the station, its distance and height.
  - The snow depth comes from one of about 75 MeteoSwiss stations, one of about 130 automatic SLF
    stations or one of about 100 SLF manual measuring sites, where an observer measures the snow by hand each
    morning from November to April. A depth measured every 10 or 30 minutes counts for 210 minutes, as
    on [SLF's map](https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/measured-values/information-about-snow-measurement/);
    an observer's counts until the next morning.
  - The new snow comes only from SLF: measured by an observer, or, at an automatic station, calculated by
    SLF from the snow depth, as old snow settles while new snow falls. Both cover the 24 hours up to
    about 06:00 UTC. The 3 days are the sum of three 24-hour values, as SLF adds them for its maps.
  - SLF says the observers' values are very accurate, but that most manual sites lie below about
    2000 m, so with moist snow or rain more new snow lies higher up.
  - Only stations within 25 km by distance plus 20 times the height gap are used, such as 15 km away and
    500 m higher, as snow changes with height and terrain. Tested on the 5817 forecast points: 94 % of
    those between 1200 and 2000 m keep a new snow station; below 800 m, where the nearest SLF station is
    often 40 km away and 600 m higher, 88 % get none, but 96 % still get a snow depth.
  - A block is null when none of its stations has a current value, and the tool says so when both are.
- `measurements_extremes` lists the first 5 stations by temperature or gust. For the wettest, it lists
  the first 5 rainy stations and counts the rainy stations in Switzerland and in each canton.
- For the sunniest it groups the stations by their sun in the last 10 minutes with the sunshine terms of
  [MeteoSwiss](https://www.meteoswiss.admin.ch/weather/weather-and-climate-from-a-to-z/sunshine.html):
  mostly sunny (8 to 10 minutes, 80 to 100 %), rather sunny (5 to 7), partly sunny (2 to 4), only a little
  sunshine (1) and hardly any sunshine (0). MeteoSwiss's ranges meet at 20, 50 and 80 %; a station
  exactly there counts for the sunnier term, so 2 minutes (20 %) is partly sunny. The cantons come in
  the order of their stations' average sun.

### Forecast Values Are Medians
- Temperature and rain are the median of the many possible outcomes MeteoSwiss calculates, not the
  most likely value.
- Medians do not add up: when showers are possible but unlikely in any 3-hour block, every block shows
  0 mm while the day's median is above 0.
- `forecast_daily` gives the rain of a day, and `forecast_rain` the chance and how much a wet hour may
  bring.

### Cache and Downloads
- The cache lives in your OS cache directory, such as `~/Library/Caches/swiss-weather-mcp` on macOS or
  `~/.cache/swiss-weather-mcp` on Linux. `SWISS_WEATHER_MCP_CACHE_DIR` moves it elsewhere.
- The first start downloads the swissNAMES3D archive (about 33 MB) and the swissBOUNDARIES3D GeoPackage
  (about 37 MB, kept unpacked at about 74 MB).
- MeteoSwiss publishes a new forecast run every hour, each hourly parameter as one file of about 30 to
  34 MB for all forecast points. By default the server downloads such a file for every new forecast
  point and keeps only that point's rows.
- With `--cache-all-forecast-points` it keeps the whole files, so further places in the same run need no
  new download. This helps when many places are asked within the hour, at the cost of disk space.

## Example Usage with LMStudio

### Using the streamable-http transport layer
Configure the mcp.json file in [LMStudio](https://lmstudio.ai/):
```json
{
  "mcpServers": {
    "swiss_weather_mcp_server": {
      "url": "http://localhost:8050/mcp/"
    }
  }
}
```
Run the MCP server with the streamable-http transport, as in [Running the Server](#running-the-server).

### Using the stdio transport layer
Configure the mcp.json file in LMStudio. Replace `<path-to>` so the command is the full path that
`which swiss-weather-mcp-server` prints:
```json
{
  "mcpServers": {
    "swiss_weather_mcp_server": {
      "command": "<path-to>/swiss-weather-mcp-server"
    }
  }
}
```
![LMStudioMCPServer](docs/LMStudioMCPServer.png)

## Resources
- [MeteoSwiss Open Data](https://www.meteoswiss.admin.ch/services-and-publications/service/open-data.html)
- [Local Forecast Notebook Examples](https://github.com/MeteoSwiss/opendata-localforecast-demos)
- [Model Context Protocol](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Server Quickstart](https://modelcontextprotocol.io/docs/develop/build-server)

## License
Licensed under the [Apache License 2.0](/LICENSE.txt).
