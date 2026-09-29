import threading
from datetime import date

import pytest

from fakes import build_swiss_time
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.forecast.parameters import PICTOGRAMS
from swiss_weather_mcp.forecast.service import _describe_pictogram


# --- read_sunshine_hours ---

@pytest.mark.asyncio
async def test_read_sunshine_hours(service_fixture):
    # 08:00 to 15:00 Swiss is 06:00 to 13:00 UTC, the rows stamped 07:00 to 13:00 UTC: 60 + 45
    # minutes. The row stamped 06:00 covers 05:00 to 06:00 UTC, before the period starts.
    answer = await service_fixture.read_sunshine_hours(
        "Zurich", build_swiss_time("2026-09-23T08:00"), build_swiss_time("2026-09-23T15:00")
    )
    assert answer["value"] == 1.8
    assert answer["unit"] == "h"
    assert answer["from"] == "2026-09-23T08:00+02:00"
    assert answer["to"] == "2026-09-23T15:00+02:00"


@pytest.mark.asyncio
async def test_read_sunshine_hours_single_hour(service_fixture):
    # 14:00 to 15:00 Swiss is the row stamped 13:00 UTC, 45 minutes. The row stamped 12:00 UTC
    # covers 13:00 to 14:00 Swiss, the hour before the period, and must not be counted.
    answer = await service_fixture.read_sunshine_hours(
        "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T15:00")
    )
    assert answer["value"] == 0.8


@pytest.mark.asyncio
async def test_read_sunshine_hours_past_the_end(service_fixture):
    # The last row is stamped 13:00 UTC, 15:00 Swiss, so a period to 16:00 would be summed short
    with pytest.raises(CannotAnswerError, match="not fully covered by the forecast"):
        await service_fixture.read_sunshine_hours(
            "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T16:00")
        )


@pytest.mark.asyncio
async def test_read_sunshine_hours_with_a_missing_hour(service_fixture, source_fixture):
    # An hour MeteoSwiss leaves empty would make the sum too low, so the period is refused
    del source_fixture.published["sre000h0"]["202609231000"]
    with pytest.raises(CannotAnswerError, match="not fully covered by the forecast"):
        await service_fixture.read_sunshine_hours(
            "Zurich", build_swiss_time("2026-09-23T08:00"), build_swiss_time("2026-09-23T15:00")
        )


@pytest.mark.asyncio
async def test_read_sunshine_hours_refuses_an_end_inside_an_hour(service_fixture):
    with pytest.raises(CannotAnswerError, match="is not a full hour"):
        await service_fixture.read_sunshine_hours(
            "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T14:30")
        )


@pytest.mark.asyncio
async def test_read_sunshine_hours_reversed_period(service_fixture):
    with pytest.raises(CannotAnswerError, match="must be after its start"):
        await service_fixture.read_sunshine_hours(
            "Zurich", build_swiss_time("2026-09-23T15:00"), build_swiss_time("2026-09-23T09:00")
        )


# --- read_total_cloud_cover ---

@pytest.mark.asyncio
async def test_read_total_cloud_cover(service_fixture):
    answer = await service_fixture.read_total_cloud_cover("Zurich", build_swiss_time("2026-09-23T14:00"))

    # Half the sky low and half high: clear only where both are clear, 1 - 0.5 * 0.5 = 75%
    assert answer["value"] == 75.0
    assert (answer["low_percent"], answer["medium_percent"], answer["high_percent"]) == (50.0, 0.0, 50.0)


@pytest.mark.asyncio
async def test_read_total_cloud_cover_refuses_a_time_inside_an_hour(service_fixture):
    with pytest.raises(CannotAnswerError, match="is not a full hour"):
        await service_fixture.read_total_cloud_cover("Zurich", build_swiss_time("2026-09-23T14:20"))


# --- read_daily_forecast ---

@pytest.mark.asyncio
async def test_read_daily_forecast(service_fixture):
    # The fixture publishes daily values for 23 and 24 September only, so 25 September is left out
    answer = await service_fixture.read_daily_forecast("Zurich", date(2026, 9, 23), 3)

    assert [day["date"] for day in answer["days"]] == ["2026-09-23", "2026-09-24"]
    assert [day["weekday"] for day in answer["days"]] == ["Wednesday", "Thursday"]
    assert [day["temperature_max_c"] for day in answer["days"]] == [20.6, 18.4]
    assert answer["location"] == "Zürich 8001 (409 m)"
    assert answer["model_run"] == "2026-09-22T15:00+02:00"


@pytest.mark.asyncio
async def test_read_daily_forecast_one_day(service_fixture):
    answer = await service_fixture.read_daily_forecast("Zurich", date(2026, 9, 24), 1)
    assert [day["date"] for day in answer["days"]] == ["2026-09-24"]


@pytest.mark.asyncio
async def test_read_daily_forecast_missing_parameter(service_fixture):
    # Only tre200px has rows in this fixture, the other daily files have none for Zurich
    answer = await service_fixture.read_daily_forecast("Zurich", date(2026, 9, 23), 1)
    assert answer["days"][0]["rainfall_median_mm"] is None


@pytest.mark.asyncio
async def test_read_daily_forecast_without_a_pictogram(service_fixture):
    # jp2000d0 has no rows for Zurich, so every day still has the weather fields, set to None
    answer = await service_fixture.read_daily_forecast("Zurich", date(2026, 9, 23), 1)
    day = answer["days"][0]
    assert (day["pictogram_code"], day["weather"], day["weather_emoji"]) == (None, None, None)


@pytest.mark.asyncio
async def test_read_daily_forecast_with_a_pictogram(service_fixture, source_fixture):
    source_fixture.published["jp2000d0"] = {"202609230000": "2"}
    answer = await service_fixture.read_daily_forecast("Zurich", date(2026, 9, 23), 1)
    day = answer["days"][0]
    assert (day["pictogram_code"], day["weather"], day["weather_emoji"]) == (2, "mostly sunny, some clouds", "🌤️")


@pytest.mark.asyncio
async def test_read_daily_forecast_invalid_days(service_fixture):
    for days in (0, 10):
        with pytest.raises(CannotAnswerError, match="between 1 and 9"):
            await service_fixture.read_daily_forecast("Zurich", date(2026, 9, 23), days)


@pytest.mark.asyncio
async def test_read_daily_forecast_outside_the_forecast(service_fixture):
    # Every parameter exists, but none reaches these days, so there is nothing to answer with
    with pytest.raises(CannotAnswerError, match="no daily forecast"):
        await service_fixture.read_daily_forecast("Zurich", date(2026, 10, 30), 3)


@pytest.mark.asyncio
async def test_read_daily_forecast_does_not_hide_a_bug(service_fixture, mocker):
    # A ValueError that is not a CannotAnswerError is a bug, not a parameter missing for this place
    mocker.patch.object(service_fixture.forecast_source, "read_series", side_effect=ValueError("could not convert"))

    with pytest.raises(ValueError, match="could not convert"):
        await service_fixture.read_daily_forecast("Zurich", date(2026, 9, 23), 1)


# --- read_wind ---

@pytest.mark.asyncio
async def test_read_wind(service_fixture):
    # 14:00 Swiss is the hour ending at 12:00 UTC
    answer = await service_fixture.read_wind("Zurich", build_swiss_time("2026-09-23T14:00"))

    assert (answer["speed_kmh"], answer["gusts_kmh"]) == (12.5, 38.0)
    assert answer["gusts_90th_percentile_kmh"] == 55.0
    assert (answer["direction_degrees"], answer["compass_point"]) == (217.0, "SW")
    assert answer["valid_at"] == "2026-09-23T14:00+02:00"
    assert answer["location"] == "Zürich 8001 (409 m)"


# --- read_hourly_forecast ---

@pytest.mark.asyncio
async def test_read_hourly_forecast(service_fixture):
    # 13:00 to 15:00 Swiss is 11:00 to 13:00 UTC: the hours ending at 12:00 and 13:00 UTC
    answer = await service_fixture.read_hourly_forecast(
        "Zurich", build_swiss_time("2026-09-23T13:00"), build_swiss_time("2026-09-23T15:00")
    )

    assert [(hour["from"], hour["to"]) for hour in answer["hours"]] == [
        ("2026-09-23T13:00+02:00", "2026-09-23T14:00+02:00"),
        ("2026-09-23T14:00+02:00", "2026-09-23T15:00+02:00"),
    ]
    assert [hour["temperature_c"] for hour in answer["hours"]] == [12.0, 14.5]
    assert [hour["temperature_10th_percentile_c"] for hour in answer["hours"]] == [10.8, 13.2]
    assert [hour["temperature_90th_percentile_c"] for hour in answer["hours"]] == [13.4, 16.1]
    assert [hour["rain_chance_percent"] for hour in answer["hours"]] == [10.0, 20.0]
    assert [(hour["weather"], hour["weather_emoji"]) for hour in answer["hours"]] == [
        ("mostly sunny, some clouds", "🌤️"),
        ("partly sunny, thick passing clouds", "⛅"),
    ]
    assert answer["location"] == "Zürich 8001 (409 m)"
    assert answer["model_run"] == "2026-09-22T15:00+02:00"


@pytest.mark.asyncio
async def test_read_hourly_forecast_one_hour(service_fixture):
    # Without an end, the one hour starting at start: 13:00 to 14:00 Swiss, the row stamped 12:00 UTC
    answer = await service_fixture.read_hourly_forecast("Zurich", build_swiss_time("2026-09-23T13:00"))

    assert [(hour["from"], hour["to"]) for hour in answer["hours"]] == [("2026-09-23T13:00+02:00", "2026-09-23T14:00+02:00")]
    assert answer["hours"][0]["temperature_c"] == 12.0


@pytest.mark.asyncio
async def test_read_hourly_forecast_refuses_a_start_inside_an_hour(service_fixture):
    # MeteoSwiss forecasts whole hours, so the model is told which full hours it can ask for
    with pytest.raises(CannotAnswerError) as raised:
        await service_fixture.read_hourly_forecast("Zurich", build_swiss_time("2026-09-23T14:30"))

    assert str(raised.value) == (
        "2026-09-23T14:30+02:00 is not a full hour. MeteoSwiss forecasts whole hours, so use a full hour "
        "such as 2026-09-23T14:00+02:00 or 2026-09-23T15:00+02:00."
    )


@pytest.mark.asyncio
async def test_read_hourly_forecast_refuses_an_end_inside_an_hour(service_fixture):
    with pytest.raises(CannotAnswerError, match="is not a full hour"):
        await service_fixture.read_hourly_forecast(
            "Zurich", build_swiss_time("2026-09-23T13:00"), build_swiss_time("2026-09-23T14:30")
        )


@pytest.mark.asyncio
async def test_read_hourly_forecast_outside_the_forecast(service_fixture):
    # Times are written as the answers write them, with the offset of their own date: 30 October is
    # already winter time, the covered range is still summer time
    with pytest.raises(CannotAnswerError, match="is not fully covered by the forecast") as raised:
        await service_fixture.read_hourly_forecast("Zurich", build_swiss_time("2026-10-30T14:00"))

    assert str(raised.value).startswith("2026-10-30T14:00+01:00 is not fully covered by the forecast")
    assert "The forecast covers 2026-09-23T14:00+02:00 to 2026-09-23T15:00+02:00." in str(raised.value)
    assert "jww003i0" not in str(raised.value)  # a parameter code means nothing to the model


@pytest.mark.asyncio
async def test_read_hourly_forecast_past_the_end(service_fixture):
    with pytest.raises(CannotAnswerError, match="not fully covered by the forecast"):
        await service_fixture.read_hourly_forecast(
            "Zurich", build_swiss_time("2026-09-23T13:00"), build_swiss_time("2026-09-23T17:00")
        )


@pytest.mark.asyncio
async def test_read_hourly_forecast_too_long(service_fixture):
    with pytest.raises(CannotAnswerError, match="at most 24 hours"):
        await service_fixture.read_hourly_forecast(
            "Zurich", build_swiss_time("2026-09-23T13:00"), build_swiss_time("2026-09-24T14:00")
        )


@pytest.mark.asyncio
async def test_read_hourly_forecast_when_the_clocks_go_back(service_fixture, source_fixture):
    # On 25 October 2026 the clocks go back from 03:00 to 02:00, so 01:00 to 04:00 Swiss is 4 hours,
    # the rows stamped 00:00 to 03:00 UTC, and the hour after 02:00 comes twice
    for parameter in ("tre200h0", "treq10h0", "treq90h0", "rp0003i0", "jww003i0"):
        for utc_hour in ("00", "01", "02", "03"):
            source_fixture.published[parameter][f"20261025{utc_hour}00"] = "1"

    answer = await service_fixture.read_hourly_forecast(
        "Zurich", build_swiss_time("2026-10-25T01:00"), build_swiss_time("2026-10-25T04:00")
    )

    assert [(hour["from"], hour["to"]) for hour in answer["hours"]] == [
        ("2026-10-25T01:00+02:00", "2026-10-25T02:00+02:00"),
        ("2026-10-25T02:00+02:00", "2026-10-25T02:00+01:00"),
        ("2026-10-25T02:00+01:00", "2026-10-25T03:00+01:00"),
        ("2026-10-25T03:00+01:00", "2026-10-25T04:00+01:00"),
    ]


# --- read_rain_outlook ---

@pytest.mark.asyncio
async def test_read_rain_outlook(service_fixture):
    # 14:00 to 20:00 Swiss is 12:00 to 18:00 UTC: two blocks, ending at 15:00 and 18:00 UTC
    answer = await service_fixture.read_rain_outlook(
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
    assert answer["location"] == "Zürich 8001 (409 m)"


@pytest.mark.asyncio
async def test_read_rain_outlook_past_the_end(service_fixture):
    with pytest.raises(CannotAnswerError, match="not fully covered by the forecast"):
        await service_fixture.read_rain_outlook(
            "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T23:00")
        )


@pytest.mark.asyncio
async def test_read_rain_outlook_too_long(service_fixture):
    with pytest.raises(CannotAnswerError, match="at most 48 hours"):
        await service_fixture.read_rain_outlook(
            "Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-25T20:00")
        )


@pytest.mark.asyncio
async def test_read_rain_outlook_refuses_a_start_inside_an_hour(service_fixture):
    with pytest.raises(CannotAnswerError, match="is not a full hour"):
        await service_fixture.read_rain_outlook(
            "Zurich", build_swiss_time("2026-09-23T14:30"), build_swiss_time("2026-09-23T20:00")
        )


@pytest.mark.asyncio
async def test_read_rain_outlook_reversed_period(service_fixture):
    with pytest.raises(CannotAnswerError, match="must be after its start"):
        await service_fixture.read_rain_outlook(
            "Zurich", build_swiss_time("2026-09-23T20:00"), build_swiss_time("2026-09-23T14:00")
        )



# --- parallel reads ---

# How long a read waits for the other reads of its tool to start
PARALLEL_READ_TIMEOUT_SECONDS = 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name, arguments, file_count", [
    ("read_wind", ("Zurich", build_swiss_time("2026-09-23T14:00")), 4),
    ("read_total_cloud_cover", ("Zurich", build_swiss_time("2026-09-23T14:00")), 3),
    ("read_hourly_forecast", ("Zurich", build_swiss_time("2026-09-23T13:00")), 5),
    ("read_rain_outlook", ("Zurich", build_swiss_time("2026-09-23T14:00"), build_swiss_time("2026-09-23T20:00")), 3),
    ("read_daily_forecast", ("Zurich", date(2026, 9, 23), 1), 6),
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


# --- _describe_pictogram ---

def test_describe_pictogram():
    assert _describe_pictogram(1) == ("sunny", "☀️")
    assert _describe_pictogram(101) == ("clear", "✨")
    assert _describe_pictogram(999) == ("unknown weather code 999", None)


def test_describe_pictogram_every_code_has_words_and_an_emoji():
    for code, (description, emoji) in PICTOGRAMS.items():
        assert description and emoji, code
