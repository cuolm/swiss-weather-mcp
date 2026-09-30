import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
import requests
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.server import SwissWeatherMCPServer, _parse_swiss_time

SWISS_TZ = ZoneInfo("Europe/Zurich")

# Every tool the server offers, with its arguments in order
EXPECTED_TOOLS = {
    "current_date_and_time": [],
    "daily_forecast": ["location", "start_date", "days"],
    "hourly_forecast": ["location", "start", "end"],
    "rain_outlook": ["location", "start", "end"],
    "sunshine_hours": ["location", "start", "end"],
    "wind": ["location", "when"],
    "total_cloud_cover": ["location", "when"],
    "freezing_level": ["location", "when"],
    "current_conditions": ["location"],
    "current_extremes": ["extreme"],
}

FREEZING_LEVEL_CALL = {"location": "Zurich", "when": "2026-09-24T14:00"}


@pytest.fixture
def server_fixture(mocker):
    """Return the MCP server with mock services, so no tool reaches the network."""
    forecast_service = mocker.AsyncMock()
    measurement_service = mocker.AsyncMock()
    return SwissWeatherMCPServer(forecast_service, measurement_service, "INFO")


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
async def test_current_extremes_refuses_an_unknown_extreme(server_fixture):
    with pytest.raises(ToolError, match="'warmest', 'coldest', 'windiest', 'wettest' or 'sunniest'"):
        await server_fixture.mcp.call_tool("current_extremes", {"extreme": "hottest"})
    server_fixture.measurement_service.read_current_extremes.assert_not_awaited()


# --- _handle_tool_call ---

@pytest.mark.asyncio
async def test_handle_tool_call_returns_the_answer(server_fixture):
    answer = {"value": 3200.0, "unit": "m above sea level", "location": "Zürich 8001 (409 m)"}
    server_fixture.forecast_service.read_freezing_level.return_value = answer

    tool_result = await server_fixture.mcp.call_tool("freezing_level", FREEZING_LEVEL_CALL)
    assert json.loads(tool_result.content[0].text) == answer


@pytest.mark.asyncio
async def test_handle_tool_call_value_error(server_fixture, caplog):
    server_fixture.forecast_service.read_freezing_level.side_effect = CannotAnswerError("Location 'Tessin' is not one of the places")

    with caplog.at_level(logging.INFO), pytest.raises(ToolError, match="Location 'Tessin' is not one of") as raised:
        await server_fixture.mcp.call_tool("freezing_level", {"location": "Tessin", "when": "2026-09-24T14:00"})

    assert not isinstance(raised.value, UnexpectedToolError)
    for record in caplog.records:
        assert record.exc_info is None, f"traceback logged by {record.name}"


@pytest.mark.asyncio
async def test_handle_tool_call_invalid_timestamp(server_fixture):
    with pytest.raises(ToolError, match="is not a valid timestamp"):
        await server_fixture.mcp.call_tool("freezing_level", {"location": "Zurich", "when": "tomorrow"})


@pytest.mark.asyncio
async def test_handle_tool_call_meteoswiss_unreachable(server_fixture):
    server_fixture.forecast_service.read_freezing_level.side_effect = requests.ConnectionError("connection refused")

    with pytest.raises(ToolError, match="Could not reach MeteoSwiss"):
        await server_fixture.mcp.call_tool("freezing_level", FREEZING_LEVEL_CALL)


@pytest.mark.asyncio
async def test_handle_tool_call_unexpected_error(server_fixture):
    # A bug is a crash: the SDK logs the traceback and tells the model nothing about the internals
    server_fixture.forecast_service.read_freezing_level.side_effect = KeyError("internal detail")

    with pytest.raises(UnexpectedToolError) as raised:
        await server_fixture.mcp.call_tool("freezing_level", FREEZING_LEVEL_CALL)
    assert "internal detail" not in str(raised.value)


@pytest.mark.asyncio
async def test_handle_tool_call_unexpected_value_error(server_fixture):
    # A ValueError that is not a CannotAnswerError is a bug too, such as a changed MeteoSwiss file
    server_fixture.forecast_service.read_freezing_level.side_effect = ValueError("could not convert string to float")

    with pytest.raises(UnexpectedToolError) as raised:
        await server_fixture.mcp.call_tool("freezing_level", FREEZING_LEVEL_CALL)
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
