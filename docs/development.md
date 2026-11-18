# Development

## From Source
```bash
git clone https://github.com/cuolm/swiss-weather-mcp.git
cd swiss-weather-mcp
uv sync --extra client  # or, in a virtual environment: pip install -e ".[client]"
```

From a source checkout, put `uv run` in front of the commands in the README, such as
`uv run swiss-weather-mcp --transport=streamable-http --host=localhost --port=8050`.

To build and run the Docker image from a source checkout:
```bash
docker build -t swiss-weather-mcp .
docker run -p 127.0.0.1:8050:8050 swiss-weather-mcp
```

## Checks
The same checks run in CI on every push:
```bash
uv run pytest      # the tests, on fake data; they never use the network
uv run ruff check  # the lint rules of [tool.ruff] in pyproject.toml
uv run mypy        # types, in strict mode
```

## Releasing
The version comes from the git tag. Pushing a tag starts a release workflow:
```bash
git tag 0.5.0
git push origin 0.5.0
```
- A tag of three numbers, such as `0.5.0`, starts `.github/workflows/release.yaml`. After the tests it
  publishes the Docker image to GHCR, the package to PyPI and the server to the MCP Registry, creates the
  GitHub release and adds the Claude Desktop bundle (`.mcpb`) to it.
- A tag with `a`, `b` or `rc`, such as `0.5.0rc1`, starts `.github/workflows/release_test.yaml`. It
  publishes the package to TestPyPI instead, only checks `server.json` and the bundle's `manifest.json`,
  and still publishes a Docker image and a GitHub pre-release under that tag.

## Running the MCP Client
The bundled MCP client tests the server over stdio. It starts the MCP server itself, but not the LLM, which has to be running first, for example in `llama-server`.

```bash
# Install the server with the client, which pulls in the OpenAI SDK
uv tool install 'swiss-weather-mcp[client]'

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

## Project Structure
```text
swiss-weather-mcp/
├── src/swiss_weather_mcp/
│   ├── server.py           # MCP server and its tools
│   ├── places/             # swisstopo place lookup
│   ├── forecast/           # MeteoSwiss local forecasts
│   ├── measurements/       # MeteoSwiss SwissMetNet and SLF IMIS measurements
│   ├── avalanche/          # SLF avalanche bulletin
│   ├── astronomy/          # Sunrise, sunset, moonrise, moonset and phase
│   ├── downloads.py        # Downloads and caches files from data.geo.admin.ch and SLF
│   ├── distance.py         # Distance plus height gap, to choose a station for a place
│   ├── polygons.py         # Whether a position lies inside polygons with holes
│   ├── coordinates.py      # WGS84 latitude and longitude to LV95 and back
│   ├── cantons.py          # The names of the cantons by their abbreviation
│   ├── formatting.py       # Times, wind directions and height differences in the answers
│   ├── errors.py           # The error for questions the data cannot answer
│   ├── log.py              # Logging setup
│   └── client.py           # MCP client
├── tests/swiss_weather_mcp/  # Tests
├── scripts/                # Print what the tools answer for a place, to look at real answers
├── docs/                   # How it works, and this page
├── mcpb/                   # Claude Desktop bundle, filled in by the release workflow
├── server.json             # Entry of the MCP Registry
├── Dockerfile              # Docker image
└── .github/workflows/      # CI and release pipelines
```

## Resources
- [MeteoSwiss Open Data](https://www.meteoswiss.admin.ch/services-and-publications/service/open-data.html)
- [Local Forecast Notebook Examples](https://github.com/MeteoSwiss/opendata-localforecast-demos)
- [Model Context Protocol](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Server Quickstart](https://modelcontextprotocol.io/docs/develop/build-server)
