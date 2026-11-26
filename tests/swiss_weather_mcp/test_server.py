import json
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import requests
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from starlette.testclient import TestClient

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.server import SwissWeatherMCPServer, _parse_swiss_time


SWISS_TZ = ZoneInfo("Europe/Zurich")

# Every tool the server offers, with its arguments in order
EXPECTED_TOOLS = {
    "forecast_daily": ["location", "start_date", "days", "near"],
    "forecast_rain": ["location", "start", "end", "near"],
    "forecast_temperature": ["location", "start", "end", "near"],
    "forecast_sunshine": ["location", "start", "end", "near"],
    "forecast_wind": ["location", "when", "near"],
    "forecast_cloud_cover": ["location", "when", "near"],
    "forecast_freezing_level": ["location", "when", "near"],
    "measurements_current": ["location", "near"],
    "measurements_history": ["location", "start_date", "days", "values", "near"],
    "measurements_snow": ["location", "near"],
    "measurements_extremes": ["extreme"],
    "avalanche_bulletin": ["location", "near"],
    "astronomy_sun": ["location", "date", "near"],
    "astronomy_moon": ["location", "date", "near"],
    "current_date_and_time": [],
}

FREEZING_LEVEL_CALL = {"location": "Zurich", "when": "2026-09-24T14:00"}
# The first request an MCP client sends over HTTP, with the headers the transport asks for
INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
}
MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


@pytest.fixture
def server_fixture(mocker):
    """Return the MCP server with mock services, so no tool reaches the network."""
    forecast_service = mocker.AsyncMock()
    measurement_service = mocker.AsyncMock()
    astronomy_service = mocker.AsyncMock()
    avalanche_service = mocker.AsyncMock()
    return SwissWeatherMCPServer(forecast_service, measurement_service, astronomy_service, avalanche_service, "INFO")


# --- _parse_swiss_time ---

def test_parse_swiss_time_without_offset():
    moment = _parse_swiss_time("2026-09-23T14:00")
    assert moment == datetime(2026, 9, 23, 14, tzinfo=SWISS_TZ)


def test_parse_swiss_time_summer():
    # In September Switzerland is on CEST, so 14:00 local is 12:00 UTC
    moment = _parse_swiss_time("2026-09-23T14:00")
    assert moment.astimezone(timezone.utc).hour == 12


def test_parse_swiss_time_winter():
    # In January Switzerland is on CET, so the same local hour is 13:00 UTC
    moment = _parse_swiss_time("2026-01-23T14:00")
    assert moment.astimezone(timezone.utc).hour == 13


def test_parse_swiss_time_keeps_an_explicit_offset():
    moment = _parse_swiss_time("2026-09-23T14:00+00:00")
    assert moment.astimezone(timezone.utc).hour == 14


def test_parse_swiss_time_date_only():
    moment = _parse_swiss_time("2026-09-23")
    assert moment == datetime(2026, 9, 23, 0, 0, tzinfo=SWISS_TZ)


def test_parse_swiss_time_with_a_space():
    assert _parse_swiss_time("2026-09-23 14:00") == _parse_swiss_time("2026-09-23T14:00")


def test_parse_swiss_time_invalid():
    with pytest.raises(CannotAnswerError, match="2026-09-23T14:00"):
        _parse_swiss_time("tomorrow afternoon")


# --- _register_tools ---

@pytest.mark.asyncio
async def test_register_tools(server_fixture):
    offered = {}
    for tool in await server_fixture.mcp.list_tools():
        offered[tool.name] = list(tool.input_schema.get("properties", {}))
    assert offered == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_register_tools_marks_every_tool_read_only(server_fixture):
    for tool in await server_fixture.mcp.list_tools():
        assert tool.annotations.read_only_hint is True
        # Only the clock tool stays off the open data portal
        assert tool.annotations.open_world_hint is (tool.name != "current_date_and_time")


@pytest.mark.asyncio
async def test_register_tools_publishes_no_output_schema_for_the_weather_tools(server_fixture):
    # The model reads the answers as text, and a schema would lengthen the tool list of every conversation
    for tool in await server_fixture.mcp.list_tools():
        if tool.name != "current_date_and_time":
            assert tool.output_schema is None


@pytest.mark.asyncio
async def test_register_tools_describes_near(server_fixture):
    for tool in await server_fixture.mcp.list_tools():
        near = tool.input_schema.get("properties", {}).get("near")
        if near is not None:
            assert 'use location "Blausee" and near "Bettmeralp"' in near["description"]
            assert "near" not in tool.input_schema["required"]


@pytest.mark.asyncio
async def test_measurements_current_passes_near_on(server_fixture):
    server_fixture.measurement_service.read_current.return_value = {"place": "Blausee (445 m)"}

    await server_fixture.mcp.call_tool("measurements_current", {"location": "Blausee", "near": "Wallisellen"})
    server_fixture.measurement_service.read_current.assert_awaited_once_with("Blausee", "Wallisellen")


@pytest.mark.asyncio
async def test_measurements_history_passes_the_days_on(server_fixture):
    server_fixture.measurement_service.read_history.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool(
        "measurements_history", {"location": "Zürich", "start_date": "2026-09-14", "days": 7}
    )
    server_fixture.measurement_service.read_history.assert_awaited_once_with("Zürich", date(2026, 9, 14), 7, None, None)


@pytest.mark.asyncio
async def test_measurements_history_passes_the_values_on(server_fixture):
    server_fixture.measurement_service.read_history.return_value = {"place": "Davos Dorf"}

    await server_fixture.mcp.call_tool(
        "measurements_history", {"location": "Davos Dorf", "start_date": "2025-01-01", "values": "snow"}
    )
    server_fixture.measurement_service.read_history.assert_awaited_once_with(
        "Davos Dorf", date(2025, 1, 1), 1, "snow", None
    )


@pytest.mark.asyncio
async def test_measurements_history_refuses_unknown_values(server_fixture):
    with pytest.raises(ToolError, match="'weather' or 'snow'"):
        await server_fixture.mcp.call_tool(
            "measurements_history", {"location": "Zürich", "start_date": "2025-01-01", "values": "rain"}
        )
    server_fixture.measurement_service.read_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_measurements_extremes_refuses_an_unknown_extreme(server_fixture):
    with pytest.raises(ToolError, match="'wettest', 'sunniest', 'deepest_snow' or 'most_new_snow'"):
        await server_fixture.mcp.call_tool("measurements_extremes", {"extreme": "hottest"})
    server_fixture.measurement_service.read_extremes.assert_not_awaited()


@pytest.mark.asyncio
async def test_avalanche_bulletin_passes_near_on(server_fixture):
    server_fixture.avalanche_service.read_bulletin.return_value = {"place": "Blausee"}

    await server_fixture.mcp.call_tool("avalanche_bulletin", {"location": "Blausee", "near": "Wallisellen"})
    server_fixture.avalanche_service.read_bulletin.assert_awaited_once_with("Blausee", "Wallisellen")


@pytest.mark.asyncio
async def test_astronomy_moon_passes_the_day_on(server_fixture):
    server_fixture.astronomy_service.read_moon.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool("astronomy_moon", {"location": "Zürich", "date": "2026-11-01"})
    server_fixture.astronomy_service.read_moon.assert_awaited_once_with("Zürich", date(2026, 11, 1), None)


@pytest.mark.asyncio
async def test_astronomy_sun_passes_the_day_on(server_fixture):
    server_fixture.astronomy_service.read_sun.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool("astronomy_sun", {"location": "Zürich", "date": "2026-10-28"})
    server_fixture.astronomy_service.read_sun.assert_awaited_once_with("Zürich", date(2026, 10, 28), None)


@pytest.mark.asyncio
async def test_forecast_daily_passes_the_first_day_on(server_fixture):
    server_fixture.forecast_service.read_daily.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool("forecast_daily", {"location": "Zürich", "start_date": "2026-09-26", "days": 2})
    server_fixture.forecast_service.read_daily.assert_awaited_once_with("Zürich", date(2026, 9, 26), 2, None)


@pytest.mark.asyncio
async def test_forecast_rain_passes_the_period_on(server_fixture):
    server_fixture.forecast_service.read_rain.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool(
        "forecast_rain", {"location": "Zürich", "start": "2026-09-23T12:00", "end": "2026-09-23T18:00"}
    )
    server_fixture.forecast_service.read_rain.assert_awaited_once_with(
        "Zürich", datetime(2026, 9, 23, 12, tzinfo=SWISS_TZ), datetime(2026, 9, 23, 18, tzinfo=SWISS_TZ), None
    )


@pytest.mark.asyncio
async def test_forecast_temperature_passes_the_period_on(server_fixture):
    server_fixture.forecast_service.read_temperature.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool(
        "forecast_temperature", {"location": "Zürich", "start": "2026-09-23T06:00", "end": "2026-09-23T12:00"}
    )
    server_fixture.forecast_service.read_temperature.assert_awaited_once_with(
        "Zürich", datetime(2026, 9, 23, 6, tzinfo=SWISS_TZ), datetime(2026, 9, 23, 12, tzinfo=SWISS_TZ), None
    )


@pytest.mark.asyncio
async def test_forecast_temperature_without_an_end_passes_none_on(server_fixture):
    server_fixture.forecast_service.read_temperature.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool("forecast_temperature", {"location": "Zürich", "start": "2026-09-23T14:00"})
    server_fixture.forecast_service.read_temperature.assert_awaited_once_with(
        "Zürich", datetime(2026, 9, 23, 14, tzinfo=SWISS_TZ), None, None
    )


@pytest.mark.asyncio
async def test_forecast_sunshine_without_an_end_passes_none_on(server_fixture):
    server_fixture.forecast_service.read_sunshine.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool("forecast_sunshine", {"location": "Zürich", "start": "2026-09-23T00:00"})
    server_fixture.forecast_service.read_sunshine.assert_awaited_once_with(
        "Zürich", datetime(2026, 9, 23, 0, tzinfo=SWISS_TZ), None, None
    )


@pytest.mark.asyncio
async def test_forecast_wind_passes_the_moment_and_near_on(server_fixture):
    server_fixture.forecast_service.read_wind.return_value = {"place": "Blausee"}

    await server_fixture.mcp.call_tool(
        "forecast_wind", {"location": "Blausee", "when": "2026-09-24T14:00", "near": "Wallisellen"}
    )
    server_fixture.forecast_service.read_wind.assert_awaited_once_with(
        "Blausee", datetime(2026, 9, 24, 14, tzinfo=SWISS_TZ), "Wallisellen"
    )


@pytest.mark.asyncio
async def test_forecast_cloud_cover_passes_the_moment_on(server_fixture):
    server_fixture.forecast_service.read_cloud_cover.return_value = {"place": "Zürich"}

    await server_fixture.mcp.call_tool("forecast_cloud_cover", {"location": "Zürich", "when": "2026-09-24T14:00"})
    server_fixture.forecast_service.read_cloud_cover.assert_awaited_once_with(
        "Zürich", datetime(2026, 9, 24, 14, tzinfo=SWISS_TZ), None
    )


@pytest.mark.asyncio
async def test_measurements_snow_passes_near_on(server_fixture):
    server_fixture.measurement_service.read_snow.return_value = {"place": "Blausee"}

    await server_fixture.mcp.call_tool("measurements_snow", {"location": "Blausee", "near": "Wallisellen"})
    server_fixture.measurement_service.read_snow.assert_awaited_once_with("Blausee", "Wallisellen")


@pytest.mark.asyncio
async def test_measurements_extremes_passes_the_extreme_on(server_fixture):
    server_fixture.measurement_service.read_extremes.return_value = {"extreme": "wettest"}

    await server_fixture.mcp.call_tool("measurements_extremes", {"extreme": "wettest"})
    server_fixture.measurement_service.read_extremes.assert_awaited_once_with("wettest")


# --- _handle_tool_call ---

@pytest.mark.asyncio
async def test_handle_tool_call_returns_the_answer(server_fixture):
    answer = {"freezing_level_m": 3200.0, "place": "Zürich (418 m)"}
    server_fixture.forecast_service.read_freezing_level.return_value = answer

    tool_result = await server_fixture.mcp.call_tool("forecast_freezing_level", FREEZING_LEVEL_CALL)
    assert json.loads(tool_result.content[0].text) == answer


@pytest.mark.asyncio
async def test_handle_tool_call_logs_the_answer_only_at_debug(server_fixture, caplog):
    # An answer can be thousands of characters long, so the normal log shows only the call
    answer = {"freezing_level_m": 3200.0, "place": "Zürich (418 m)"}
    server_fixture.forecast_service.read_freezing_level.return_value = answer

    with caplog.at_level(logging.DEBUG, logger="swiss_weather_mcp.server"):
        await server_fixture.mcp.call_tool("forecast_freezing_level", FREEZING_LEVEL_CALL)

    info_messages = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    debug_messages = [record.getMessage() for record in caplog.records if record.levelno == logging.DEBUG]
    assert any("forecast_freezing_level" in message and "Zurich" in message for message in info_messages)
    assert not any("3200.0" in message for message in info_messages)
    assert any("3200.0" in message for message in debug_messages)


@pytest.mark.asyncio
async def test_handle_tool_call_cannot_answer_error(server_fixture, caplog):
    unknown_place_error = CannotAnswerError("Location 'Tessin' is not one of the places")
    server_fixture.forecast_service.read_freezing_level.side_effect = unknown_place_error

    with caplog.at_level(logging.INFO), pytest.raises(ToolError, match="Location 'Tessin' is not one of") as raised:
        await server_fixture.mcp.call_tool(
            "forecast_freezing_level", {"location": "Tessin", "when": "2026-09-24T14:00"}
        )

    assert not isinstance(raised.value, UnexpectedToolError)
    for record in caplog.records:
        assert record.exc_info is None, f"traceback logged by {record.name}"


@pytest.mark.asyncio
async def test_handle_tool_call_invalid_timestamp(server_fixture):
    with pytest.raises(ToolError, match="is not a valid timestamp"):
        await server_fixture.mcp.call_tool("forecast_freezing_level", {"location": "Zurich", "when": "tomorrow"})


@pytest.mark.asyncio
async def test_handle_tool_call_portal_unreachable(server_fixture):
    server_fixture.forecast_service.read_freezing_level.side_effect = requests.ConnectionError("connection refused")

    with pytest.raises(ToolError, match="Could not download the data"):
        await server_fixture.mcp.call_tool("forecast_freezing_level", FREEZING_LEVEL_CALL)


@pytest.mark.asyncio
async def test_handle_tool_call_unexpected_error(server_fixture):
    # A bug is a crash: the SDK logs the traceback and tells the model nothing about the internals
    server_fixture.forecast_service.read_freezing_level.side_effect = KeyError("internal detail")

    with pytest.raises(UnexpectedToolError) as raised:
        await server_fixture.mcp.call_tool("forecast_freezing_level", FREEZING_LEVEL_CALL)
    assert "internal detail" not in str(raised.value)


@pytest.mark.asyncio
async def test_handle_tool_call_unexpected_value_error(server_fixture):
    # A ValueError that is not a CannotAnswerError is a bug too, such as a changed MeteoSwiss file
    server_fixture.forecast_service.read_freezing_level.side_effect = ValueError("could not convert string to float")

    with pytest.raises(UnexpectedToolError) as raised:
        await server_fixture.mcp.call_tool("forecast_freezing_level", FREEZING_LEVEL_CALL)
    assert "could not convert" not in str(raised.value)


# --- current_date_and_time ---

@pytest.mark.asyncio
async def test_current_date_and_time_format(server_fixture):
    # The model builds its next timestamp from this answer, so it must be in the form the tools read
    tool_result = await server_fixture.mcp.call_tool("current_date_and_time", {})
    text = tool_result.content[0].text
    weekday, timestamp = text.removeprefix("Today is ").removesuffix(" (Swiss time)").split(", ")

    moment = _parse_swiss_time(timestamp)
    assert moment.tzinfo is SWISS_TZ, "written without an offset, so read as Swiss time"
    assert moment.strftime("%A") == weekday


# --- run ---

def build_http_app(mocker, server_fixture, host: str):
    """Build the HTTP app run() serves on host, without starting the web server."""
    serve_mock = mocker.patch("uvicorn.Server.serve", autospec=True)
    server_fixture.run("streamable-http", host, 8050)
    uvicorn_server = serve_mock.call_args.args[0]
    return uvicorn_server.config.app


def send_initialize_request(app, headers: dict[str, str]) -> int:
    """Send the first MCP request to the app with extra headers, and return the status code."""
    with TestClient(app) as client:
        response = client.post("/mcp", json=INITIALIZE_REQUEST, headers={**MCP_HEADERS, **headers})
    return response.status_code


def test_run_answers_a_request_for_localhost_in_docker(mocker, server_fixture):
    # The Docker image listens on 0.0.0.0, where mcp would not check the Host by itself
    app = build_http_app(mocker, server_fixture, "0.0.0.0")
    assert send_initialize_request(app, {"Host": "localhost:8050"}) == 200


def test_run_refuses_a_request_for_another_host_in_docker(mocker, server_fixture):
    # A website that points its own name at this computer sends its own name as the Host
    app = build_http_app(mocker, server_fixture, "0.0.0.0")
    assert send_initialize_request(app, {"Host": "evil.example:8050"}) == 421


def test_run_refuses_a_request_from_another_website_in_docker(mocker, server_fixture):
    app = build_http_app(mocker, server_fixture, "0.0.0.0")
    headers = {"Host": "localhost:8050", "Origin": "http://evil.example"}
    assert send_initialize_request(app, headers) == 403
