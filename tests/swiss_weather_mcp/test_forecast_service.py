import threading
from datetime import date

import pytest

from fakes import build_swiss_time
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.forecast.parameters import PICTOGRAM_CODE_TO_WEATHER
from swiss_weather_mcp.forecast.service import _build_forecast_point_fields, _describe_pictogram
from swiss_weather_mcp.forecast.source import MeteoSwissPoint
from swiss_weather_mcp.places import SwisstopoPlace


# --- read_daily ---

@pytest.mark.asyncio
async def test_read_daily(service_fixture):
    # The fixture publishes daily values for 23 and 24 September only, so 25 September is left out
    answer = await service_fixture.read_daily("Zurich", date(2026, 9, 23), 3)

    assert [day["date"] for day in answer["days"]] == ["2026-09-23", "2026-09-24"]
    assert [day["weekday"] for day in answer["days"]] == ["Wednesday", "Thursday"]
    assert [day["temperature_max_c"] for day in answer["days"]] == [20.6, 18.4]
    assert answer["place"] == "Zürich"
    assert answer["model_run"] == "2026-09-22T15:00+02:00"


@pytest.mark.asyncio
async def test_read_daily_one_day(service_fixture):
    answer = await service_fixture.read_daily("Zurich", date(2026, 9, 24), 1)
    assert [day["date"] for day in answer["days"]] == ["2026-09-24"]


@pytest.mark.asyncio
async def test_read_daily_missing_parameter(service_fixture):
    # Only tre200px has rows in this fixture, the other daily files have none for Zurich
    answer = await service_fixture.read_daily("Zurich", date(2026, 9, 23), 1)
    assert answer["days"][0]["rainfall_median_mm"] is None


@pytest.mark.asyncio
async def test_read_daily_without_a_pictogram(service_fixture):
    # jp2000d0 has no rows for Zurich, so every day still has the weather fields, set to None
    answer = await service_fixture.read_daily("Zurich", date(2026, 9, 23), 1)
    day = answer["days"][0]
    assert (day["pictogram_code"], day["weather"], day["weather_emoji"]) == (None, None, None)


@pytest.mark.asyncio
async def test_read_daily_with_a_pictogram(service_fixture, source_fixture):
    source_fixture.published["jp2000d0"] = {"202609230000": "2"}
    answer = await service_fixture.read_daily("Zurich", date(2026, 9, 23), 1)
    day = answer["days"][0]
    assert (day["pictogram_code"], day["weather"], day["weather_emoji"]) == (2, "mostly sunny, some clouds", "🌤️")


@pytest.mark.asyncio
async def test_read_daily_invalid_days(service_fixture):
    for days in (0, 10):
        with pytest.raises(CannotAnswerError, match="between 1 and 9"):
            await service_fixture.read_daily("Zurich", date(2026, 9, 23), days)


@pytest.mark.asyncio
async def test_read_daily_outside_the_forecast(service_fixture):
    # Every parameter exists, but none reaches these days, so there is nothing to answer with
    with pytest.raises(CannotAnswerError, match="no daily forecast"):
        await service_fixture.read_daily("Zurich", date(2026, 10, 30), 3)


@pytest.mark.asyncio
async def test_read_daily_does_not_hide_a_bug(service_fixture, mocker):
    # A ValueError that is not a CannotAnswerError is a bug, not a parameter missing for this place
    mocker.patch.object(service_fixture.forecast_source, "read_series", side_effect=ValueError("could not convert"))

    with pytest.raises(ValueError, match="could not convert"):
        await service_fixture.read_daily("Zurich", date(2026, 9, 23), 1)


# --- read_rain ---

@pytest.mark.asyncio
async def test_read_rain(service_fixture):
    # 14:00 to 20:00 Swiss is 12:00 to 18:00 UTC: two blocks, ending at 15:00 and 18:00 UTC
    answer = await service_fixture.read_rain(
        "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T20:00")
    )

    assert [(block["from"], block["to"]) for block in answer["blocks"]] == [
        ("2026-09-23T14:00+02:00", "2026-09-23T17:00+02:00"),
        ("2026-09-23T17:00+02:00", "2026-09-23T20:00+02:00"),
    ]
    assert [block["rain_chance_percent"] for block in answer["blocks"]] == [40.0, 80.0]
    assert [block["rainfall_median_mm"] for block in answer["blocks"]] == [0.0, 2.4]
    # The heaviest hour of each block, not a sum: quantiles do not add up
    assert [block["heaviest_hour_up_to_mm"] for block in answer["blocks"]] == [1.1, 3.5]
    # The weather icon covers the same 3 hours as the block it ends
    assert [(block["weather"], block["weather_emoji"]) for block in answer["blocks"]] == [
        ("sunny intervals, isolated showers", "🌦️"),
        ("overcast, some rain showers", "🌧️"),
    ]
    assert answer["place"] == "Zürich"


@pytest.mark.asyncio
async def test_read_rain_past_the_end(service_fixture):
    with pytest.raises(CannotAnswerError, match="not fully covered by the forecast"):
        await service_fixture.read_rain(
            "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T23:00")
        )



@pytest.mark.asyncio
async def test_read_rain_up_to_the_end_of_the_forecast(service_fixture):
    # The forecast ends at 20:00 Swiss, so the third block, 20:00 to 23:00, is left out
    answer = await service_fixture.read_rain(
        "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T21:00")
    )

    assert [(block["from"], block["to"]) for block in answer["blocks"]] == [
        ("2026-09-23T14:00+02:00", "2026-09-23T17:00+02:00"),
        ("2026-09-23T17:00+02:00", "2026-09-23T20:00+02:00"),
    ]


@pytest.mark.asyncio
async def test_read_rain_refuses_a_start_inside_an_hour(service_fixture):
    with pytest.raises(CannotAnswerError, match="is not a full hour"):
        await service_fixture.read_rain(
            "Zurich", build_swiss_time("2026-09-23T14:30"), build_swiss_time("2026-09-23T20:00")
        )


@pytest.mark.asyncio
async def test_read_rain_reversed_period(service_fixture):
    with pytest.raises(CannotAnswerError, match="must be after its start"):
        await service_fixture.read_rain(
            "Zurich", build_swiss_time("2026-09-23T20:00"), build_swiss_time("2026-09-23T14:00")
        )


# --- read_temperature ---

@pytest.mark.asyncio
async def test_read_temperature(service_fixture):
    # 13:00 to 15:00 Swiss is 11:00 to 13:00 UTC: the hours ending at 12:00 and 13:00 UTC
    answer = await service_fixture.read_temperature(
        "Zurich", build_swiss_time("2026-09-23T13:00"), build_swiss_time("2026-09-23T15:00")
    )

    assert [(hour["from"], hour["to"]) for hour in answer["hours"]] == [
        ("2026-09-23T13:00+02:00", "2026-09-23T14:00+02:00"),
        ("2026-09-23T14:00+02:00", "2026-09-23T15:00+02:00"),
    ]
    assert [hour["temperature_c"] for hour in answer["hours"]] == [12.0, 14.5]
    assert [hour["temperature_10th_percentile_c"] for hour in answer["hours"]] == [10.8, 13.2]
    assert [hour["temperature_90th_percentile_c"] for hour in answer["hours"]] == [13.4, 16.1]
    assert set(answer["hours"][0]) == {
        "from", "to", "temperature_c", "temperature_10th_percentile_c", "temperature_90th_percentile_c",
    }
    assert answer["place"] == "Zürich"
    assert answer["model_run"] == "2026-09-22T15:00+02:00"


@pytest.mark.asyncio
async def test_read_temperature_one_hour(service_fixture):
    # Without an end, the one hour starting at start: 13:00 to 14:00 Swiss, the row stamped 12:00 UTC
    answer = await service_fixture.read_temperature("Zurich", build_swiss_time("2026-09-23T13:00"))

    assert [(hour["from"], hour["to"]) for hour in answer["hours"]] == [("2026-09-23T13:00+02:00", "2026-09-23T14:00+02:00")]
    assert answer["hours"][0]["temperature_c"] == 12.0


@pytest.mark.asyncio
async def test_read_temperature_refuses_a_start_inside_an_hour(service_fixture):
    # MeteoSwiss forecasts whole hours, so the model is told which full hours it can ask for
    with pytest.raises(CannotAnswerError) as raised:
        await service_fixture.read_temperature("Zurich", build_swiss_time("2026-09-23T14:30"))

    assert str(raised.value) == (
        "2026-09-23T14:30+02:00 is not a full hour. MeteoSwiss forecasts whole hours, so use a full hour "
        "such as 2026-09-23T14:00+02:00 or 2026-09-23T15:00+02:00."
    )


@pytest.mark.asyncio
async def test_read_temperature_refuses_an_end_inside_an_hour(service_fixture):
    with pytest.raises(CannotAnswerError, match="is not a full hour"):
        await service_fixture.read_temperature(
            "Zurich", build_swiss_time("2026-09-23T13:00"), build_swiss_time("2026-09-23T14:30")
        )


@pytest.mark.asyncio
async def test_read_temperature_outside_the_forecast(service_fixture):
    # Times are written as the answers write them, with the offset of their own date: 30 October is
    # already winter time, the covered range is still summer time
    with pytest.raises(CannotAnswerError, match="is not fully covered by the forecast") as raised:
        await service_fixture.read_temperature("Zurich", build_swiss_time("2026-10-30T14:00"))

    assert str(raised.value).startswith("2026-10-30T14:00+01:00 is not fully covered by the forecast")
    assert "The forecast covers 2026-09-23T08:00+02:00 to 2026-09-23T15:00+02:00." in str(raised.value)
    assert "tre200h0" not in str(raised.value)  # a parameter code means nothing to the model


@pytest.mark.asyncio
async def test_read_temperature_past_the_end(service_fixture):
    with pytest.raises(CannotAnswerError, match="not fully covered by the forecast"):
        await service_fixture.read_temperature(
            "Zurich", build_swiss_time("2026-09-23T13:00"), build_swiss_time("2026-09-23T17:00")
        )



@pytest.mark.asyncio
async def test_read_temperature_when_the_clocks_go_back(service_fixture, source_fixture):
    # On 25 October 2026 the clocks go back from 03:00 to 02:00, so 01:00 to 04:00 Swiss is 4 hours,
    # the rows stamped 00:00 to 03:00 UTC, and the hour after 02:00 comes twice
    for parameter in ("tre200h0", "treq10h0", "treq90h0"):
        for utc_hour in ("00", "01", "02", "03"):
            source_fixture.published[parameter][f"20261025{utc_hour}00"] = "1"

    answer = await service_fixture.read_temperature(
        "Zurich", build_swiss_time("2026-10-25T01:00"), build_swiss_time("2026-10-25T04:00")
    )

    assert [(hour["from"], hour["to"]) for hour in answer["hours"]] == [
        ("2026-10-25T01:00+02:00", "2026-10-25T02:00+02:00"),
        ("2026-10-25T02:00+02:00", "2026-10-25T02:00+01:00"),
        ("2026-10-25T02:00+01:00", "2026-10-25T03:00+01:00"),
        ("2026-10-25T03:00+01:00", "2026-10-25T04:00+01:00"),
    ]


# --- read_sunshine ---

@pytest.mark.asyncio
async def test_read_sunshine(service_fixture):
    # 08:00 to 15:00 Swiss is 06:00 to 13:00 UTC, the rows stamped 07:00 to 13:00 UTC: 60 + 45
    # minutes. The row stamped 06:00 covers 05:00 to 06:00 UTC, before the period starts.
    answer = await service_fixture.read_sunshine(
        "Zurich", build_swiss_time("2026-09-23T08:00"), build_swiss_time("2026-09-23T15:00")
    )
    assert answer["value"] == 1.8
    assert answer["unit"] == "h"
    assert answer["from"] == "2026-09-23T08:00+02:00"
    assert answer["to"] == "2026-09-23T15:00+02:00"


@pytest.mark.asyncio
async def test_read_sunshine_single_hour(service_fixture):
    # 14:00 to 15:00 Swiss is the row stamped 13:00 UTC, 45 minutes. The row stamped 12:00 UTC
    # covers 13:00 to 14:00 Swiss, the hour before the period, and must not be counted.
    answer = await service_fixture.read_sunshine(
        "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T15:00")
    )
    assert answer["value"] == 0.8


@pytest.mark.asyncio
async def test_read_sunshine_without_an_end(service_fixture):
    # Without an end, the one hour starting at start: 14:00 to 15:00 Swiss, the row stamped 13:00 UTC
    answer = await service_fixture.read_sunshine("Zurich", build_swiss_time("2026-09-23T14:00"))

    assert answer["value"] == 0.8
    assert (answer["from"], answer["to"]) == ("2026-09-23T14:00+02:00", "2026-09-23T15:00+02:00")


@pytest.mark.asyncio
async def test_read_sunshine_past_the_end(service_fixture):
    # The last row is stamped 13:00 UTC, 15:00 Swiss, so a period to 16:00 would be summed short
    with pytest.raises(CannotAnswerError, match="not fully covered by the forecast"):
        await service_fixture.read_sunshine(
            "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T16:00")
        )


@pytest.mark.asyncio
async def test_read_sunshine_with_a_missing_hour(service_fixture, source_fixture):
    # An hour MeteoSwiss leaves empty would make the sum too low, so the period is refused
    del source_fixture.published["sre000h0"]["202609231000"]
    with pytest.raises(CannotAnswerError, match="not fully covered by the forecast"):
        await service_fixture.read_sunshine(
            "Zurich", build_swiss_time("2026-09-23T08:00"), build_swiss_time("2026-09-23T15:00")
        )


@pytest.mark.asyncio
async def test_read_sunshine_refuses_an_end_inside_an_hour(service_fixture):
    with pytest.raises(CannotAnswerError, match="is not a full hour"):
        await service_fixture.read_sunshine(
            "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T14:30")
        )


@pytest.mark.asyncio
async def test_read_sunshine_reversed_period(service_fixture):
    with pytest.raises(CannotAnswerError, match="must be after its start"):
        await service_fixture.read_sunshine(
            "Zurich", build_swiss_time("2026-09-23T15:00"), build_swiss_time("2026-09-23T09:00")
        )


# --- read_wind ---

@pytest.mark.asyncio
async def test_read_wind(service_fixture):
    # 14:00 Swiss is the hour ending at 12:00 UTC
    answer = await service_fixture.read_wind("Zurich", build_swiss_time("2026-09-23T14:00"))

    assert (answer["speed_kmh"], answer["gusts_kmh"]) == (12.5, 38.0)
    assert answer["gusts_90th_percentile_kmh"] == 55.0
    assert (answer["direction_degrees"], answer["compass_point"]) == (217.0, "SW")
    assert answer["valid_at"] == "2026-09-23T14:00+02:00"
    assert answer["place"] == "Zürich"


@pytest.mark.asyncio
async def test_read_wind_names_the_forecast_point(service_fixture):
    # The forecast for Lindenhof is the one MeteoSwiss makes for Zürich 8001 next to it, so close
    # that the answer does not name it
    answer = await service_fixture.read_wind("Lindenhof", build_swiss_time("2026-09-23T14:00"))

    assert "data_from" not in answer
    assert answer["place"] == "Lindenhof"
    assert "forecast_point" not in answer
    assert answer["speed_kmh"] == 12.5


# --- read_cloud_cover ---

@pytest.mark.asyncio
async def test_read_cloud_cover(service_fixture):
    answer = await service_fixture.read_cloud_cover("Zurich", build_swiss_time("2026-09-23T14:00"))

    # Half the sky low and half high: clear only where both are clear, 1 - 0.5 * 0.5 = 75%
    assert answer["value"] == 75.0
    assert (answer["low_percent"], answer["medium_percent"], answer["high_percent"]) == (50.0, 0.0, 50.0)


@pytest.mark.asyncio
async def test_read_cloud_cover_refuses_a_time_inside_an_hour(service_fixture):
    with pytest.raises(CannotAnswerError, match="is not a full hour"):
        await service_fixture.read_cloud_cover("Zurich", build_swiss_time("2026-09-23T14:20"))


# --- answer order ---

@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, arguments", [
    ("read_sunshine", ("Zurich", build_swiss_time("2026-09-23T14:00"))),
    ("read_wind", ("Zurich", build_swiss_time("2026-09-23T14:00"))),
    ("read_cloud_cover", ("Zurich", build_swiss_time("2026-09-23T14:00"))),
    ("read_freezing_level", ("Zurich", build_swiss_time("2026-09-23T14:00"))),
])
async def test_answer_starts_with_the_place_and_ends_with_the_model_run(service_fixture, method_name, arguments):
    # A model reads the answer from the top, so where the values come from comes first
    answer = await getattr(service_fixture, method_name)(*arguments)

    field_names = list(answer)
    assert (field_names[0], field_names[-1]) == ("place", "model_run")


# --- parallel reads ---

# How long a read waits for the other reads of its tool to start
PARALLEL_READ_TIMEOUT_SECONDS = 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, arguments, file_count", [
    ("read_daily", ("Zurich", date(2026, 9, 23), 1), 6),
    ("read_rain", ("Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T20:00")), 4),
    ("read_temperature", ("Zurich", build_swiss_time("2026-09-23T13:00")), 3),
    ("read_wind", ("Zurich", build_swiss_time("2026-09-23T14:00")), 4),
    ("read_cloud_cover", ("Zurich", build_swiss_time("2026-09-23T14:00")), 3),
])
async def test_read_files_in_parallel(mocker, service_fixture, method_name, arguments, file_count):
    # Each read waits until all reads of the tool have started, so reads one after the other time out
    all_reads_started = threading.Barrier(file_count, timeout=PARALLEL_READ_TIMEOUT_SECONDS)
    read_series = service_fixture.forecast_source.read_series

    def wait_for_the_other_reads(parameter, point):
        all_reads_started.wait()
        return read_series(parameter, point)

    mocker.patch.object(service_fixture.forecast_source, "read_series", side_effect=wait_for_the_other_reads)
    read_forecast = getattr(service_fixture, method_name)
    await read_forecast(*arguments)


# --- _build_forecast_point_fields ---

def _build_place(altitude_m: float) -> SwisstopoPlace:
    return SwisstopoPlace("Lindenhof", ("Quartierteil",), 5, False, altitude_m, 2683200.0, 1247500.0)


def _build_point(altitude_m: float, east_m: float) -> MeteoSwissPoint:
    return MeteoSwissPoint("800100", "2", "Zürich", "8001", altitude_m, east_m, 1247500.0)


def test_build_forecast_point_fields_leaves_out_the_point_up_to_the_limits():
    fields = _build_forecast_point_fields(_build_place(400.0), _build_point(450.0, 2684200.0))

    assert fields == {"place": "Lindenhof"}


def test_build_forecast_point_fields_names_a_point_far_away():
    fields = _build_forecast_point_fields(_build_place(400.0), _build_point(400.0, 2684300.0))

    assert fields == {
        "data_from": (
            "The forecast for Lindenhof (400 m) is the one MeteoSwiss makes for its forecast point "
            "Zürich 8001 (400 m), 1.1 km away and at the same height."
        ),
        "place": "Lindenhof",
    }


def test_build_forecast_point_fields_names_a_point_much_higher():
    fields = _build_forecast_point_fields(_build_place(400.0), _build_point(451.0, 2683200.0))

    assert fields["data_from"] == (
        "The forecast for Lindenhof (400 m) is the one MeteoSwiss makes for its forecast point "
        "Zürich 8001 (451 m), 0.0 km away and 51 m higher."
    )


# --- _describe_pictogram ---

def test_describe_pictogram():
    assert _describe_pictogram(1) == ("sunny", "☀️")
    assert _describe_pictogram(101) == ("clear", "✨")
    assert _describe_pictogram(999) == ("unknown weather code 999", None)


def test_describe_pictogram_every_code_has_words_and_an_emoji():
    for code, (description, emoji) in PICTOGRAM_CODE_TO_WEATHER.items():
        assert description and emoji, code
