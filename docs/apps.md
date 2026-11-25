# Setup in AI Apps
Each app starts the server with `uvx`, so first install
[uv](https://docs.astral.sh/uv/getting-started/installation/).
If an app cannot find `uvx`, use the full path that `which uvx` prints.

## pi + llama.cpp
Everything runs on your computer: the model runs in llama.cpp, so your questions reach no cloud AI.
The server only downloads the public weather data.

Install [llama.cpp](https://llama.app/) with Homebrew or curl, then start it:
```bash
brew install --cask llama-app
# or: curl -LsSf https://llama.app/install.sh | sh
llama serve
```
Add the server to pi:
```bash
pi mcp add swiss-weather --exposure direct -- uvx swiss-weather-mcp@latest
```
In pi, connect with `/login llama.cpp`, load a model such as Gemma 4 E2B with `/llama` and select it with
`/model`.

<!-- setup video -->

## Claude Desktop
**Desktop extension:** download the
[desktop extension](https://github.com/cuolm/swiss-weather-mcp/releases/latest/download/swiss-weather-mcp.mcpb),
double-click it and select Install.

**Plugin:** go to Customize > Plugins > Add > Add marketplace, enter `cuolm/claude-plugins` and add
Swiss Weather.

<!-- setup video -->

## Unsloth Studio
In a chat, click MCP > Manage MCP servers > Add server. Enter `uvx` as the command and
`swiss-weather-mcp@latest` as the argument, as in the video.

<!-- setup video -->
