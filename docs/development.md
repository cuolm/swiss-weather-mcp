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
│   ├── formatting.py       # Times, wind directions and height differences in the answers
│   ├── errors.py           # The error for questions the data cannot answer
│   ├── log.py              # Logging setup
│   └── client.py           # MCP client
├── tests/swiss_weather_mcp/  # Tests
└── .github/workflows/      # CI and release pipelines
```

## Resources
- [MeteoSwiss Open Data](https://www.meteoswiss.admin.ch/services-and-publications/service/open-data.html)
- [Local Forecast Notebook Examples](https://github.com/MeteoSwiss/opendata-localforecast-demos)
- [Model Context Protocol](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Server Quickstart](https://modelcontextprotocol.io/docs/develop/build-server)
