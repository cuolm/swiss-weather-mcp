from datetime import date, datetime, timedelta, timezone

import pytest
import requests

from fakes import MEASUREMENTS_NOW, SLF_STATION_CODE_TO_ROWS
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.measurements import parameters
from swiss_weather_mcp.measurements.meteoswiss_source import MeteoSwissStation, MeteoSwissStationMeasurements

FLUNTERN = MeteoSwissStation(
    abbr="SMA", name="Zürich / Fluntern", canton="Zurich", altitude_m=604.0, east_m=2685223.0, north_m=1248410.0,
    data_since=date(1864, 1, 1), is_manual_site=False,
)


def build_measurements(utc_time: str, parameter_to_value: dict) -> MeteoSwissStationMeasurements:
    measured_at = datetime.fromisoformat(utc_time).replace(tzinfo=timezone.utc)
    return MeteoSwissStationMeasurements(FLUNTERN, measured_at, parameter_to_value)


def count_downloads(source_fixture, url_part: str) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if url_part in call.args[0]:
            downloads += 1
    return downloads


# --- read_current ---

@pytest.mark.asyncio
async def test_read_current(measurement_service_fixture):
    answer = await measurement_service_fixture.read_current("Zurich")

    assert answer == {
        "data_from": (
            "The measurements for Zürich (418 m) are those of the MeteoSwiss station Zürich / Fluntern (604 m), "
            "2.1 km away and 186 m higher. It measured the following values at 16:00."
        ),
        "place": "Zürich",
        "measured_at": "2026-09-25T16:00+02:00",
        "temperature_c": 21.0,
        "humidity_percent": 34.3,
        "dew_point_c": 4.7,
        "rain_last_10_minutes_mm": 0.0,
        "sunshine_last_10_minutes_min": 10.0,
        "wind_speed_kmh": 4.7,
        "gusts_kmh": 10.1,
        "wind_direction_degrees": 23.0,
        "pressure_sea_level_hpa": 1021.6,
        "compass_point": "NNE",
        "temperature_change_last_3_hours_c": 2.6,
        "pressure_change_last_3_hours_hpa": -1.2,
        "sunshine_last_hour_min": 44.0,
        "rain_last_hour_mm": 0.0,
        "strongest_gust_last_hour_kmh": 10.1,
    }


@pytest.mark.asyncio
async def test_read_current_values_the_station_lacks(measurement_service_fixture):
    # The Davos station, next to Davos Dorf, has no rain or direction here
    answer = await measurement_service_fixture.read_current("Davos Dorf")

    assert answer["rain_last_10_minutes_mm"] is None
    assert (answer["wind_direction_degrees"], answer["compass_point"]) == (None, None)
    # The station, 0.5 km away and 30 m higher, stands for the place, so the answer does not name it
    assert "data_from" not in answer


@pytest.mark.asyncio
async def test_read_current_without_earlier_measurements(measurement_service_fixture):
    # The Davos station has published only one row, so nothing to compare with and no full hour
    answer = await measurement_service_fixture.read_current("Davos Dorf")

    assert answer["temperature_c"] == 16.5
    assert answer["temperature_change_last_3_hours_c"] is None
    assert answer["pressure_change_last_3_hours_hpa"] is None
    assert answer["sunshine_last_hour_min"] is None
    assert answer["rain_last_hour_mm"] is None
    assert answer["strongest_gust_last_hour_kmh"] is None


@pytest.mark.asyncio
async def test_read_current_adds_an_slf_station_in_the_mountains(measurement_service_fixture):
    # The MeteoSwiss stations of the fakes are all far below the summit, and La Cassorte is the first SLF
    # station with a temperature in the last hour. Its wind is 2.318 m/s, its gust 4.557 m/s.
    answer = await measurement_service_fixture.read_current("Matterhorn")

    assert answer["slf_station"] == {
        "data_from": (
            "The SLF station La Cassorte (3300 m), 18.5 km away and 1178 m lower, suits Matterhorn (4478 m) "
            "better in distance and height than the MeteoSwiss station. Its values cover the 30 minutes up "
            "to 16:00."
        ),
        "measured_at": "2026-09-25T16:00+02:00",
        "temperature_c": -0.7,
        "humidity_percent": 73.6,
        "wind_speed_kmh": 8.3,
        "wind_direction_degrees": 198.8,
        "compass_point": "SSW",
        "strongest_gust_last_30_minutes_kmh": 16.4,
        "snow_surface_temperature_c": None,
    }


@pytest.mark.asyncio
async def test_read_current_without_an_slf_station_in_the_lowlands(measurement_service_fixture):
    # Fluntern, 2.1 km away and 186 m higher, suits Zürich better than any SLF station
    answer = await measurement_service_fixture.read_current("Zurich")

    assert "slf_station" not in answer


@pytest.mark.asyncio
async def test_read_current_without_recent_slf_measurements(mocker, measurement_service_fixture):
    # With La Cassorte's rows as old as Gornergratsee's, none of the first 3 SLF stations has a recent temperature
    mocker.patch.dict(SLF_STATION_CODE_TO_ROWS, {"ARO1": SLF_STATION_CODE_TO_ROWS["GOR2"]})
    answer = await measurement_service_fixture.read_current("Matterhorn")

    assert "slf_station" not in answer
    assert answer["temperature_c"] is not None


@pytest.mark.asyncio
async def test_read_current_when_slf_cannot_be_reached(mocker, measurement_service_fixture):
    mocker.patch.object(
        measurement_service_fixture.slf_source, "list_stations",
        side_effect=requests.ConnectionError("SLF is down"),
    )
    answer = await measurement_service_fixture.read_current("Matterhorn")

    assert "slf_station" not in answer
    assert answer["temperature_c"] is not None


@pytest.mark.asyncio
async def test_read_current_for_a_district(measurement_service_fixture):
    # The station is found from the district itself, not from the town around it
    answer = await measurement_service_fixture.read_current("Lindenhof")

    assert answer["data_from"].startswith(
        "The measurements for Lindenhof (428 m) are those of the MeteoSwiss station Zürich / Fluntern (604 m), "
        "2.2 km away and 176 m higher."
    )
    assert answer["place"] == "Lindenhof"


# --- read_history ---

@pytest.mark.asyncio
async def test_read_history(measurement_service_fixture):
    answer = await measurement_service_fixture.read_history("Zurich", date(2026, 9, 23), 2)

    assert answer == {
        "place": "Zürich",
        "weather": {
            "data_from": (
                "The measurements for Zürich (418 m) are those of the MeteoSwiss station Zürich / Fluntern (604 m), "
                "2.1 km away and 186 m higher."
            ),
            "days": [
                {
                    "date": "2026-09-23",
                    "weekday": "Wednesday",
                    "temperature_mean_c": 14.3,
                    "temperature_min_c": 6.9,
                    "temperature_max_c": 21.4,
                    "rain_mm": 0.0,
                    "sunshine_h": 11.1,
                    "strongest_gust_kmh": 20.5,
                },
                {
                    "date": "2026-09-24",
                    "weekday": "Thursday",
                    "temperature_mean_c": 15.3,
                    "temperature_min_c": 10.9,
                    "temperature_max_c": 21.8,
                    "rain_mm": 0.0,
                    "sunshine_h": 3.0,
                    "strongest_gust_kmh": 30.2,
                },
            ],
        },
        "snow": {
            "data_from": (
                "The snow for Zürich (418 m) was measured by an observer at the MeteoSwiss station "
                "Zürich / Fluntern (604 m), 2.1 km away and 186 m higher."
            ),
            "days": [
                {"date": "2026-09-23", "snow_depth_cm": 0.0, "new_snow_cm": 0.0},
                {"date": "2026-09-24", "snow_depth_cm": 0.0, "new_snow_cm": 0.0},
            ],
        },
    }


@pytest.mark.asyncio
async def test_read_history_values_the_station_lacks(measurement_service_fixture):
    # Fluntern has no gust and only the rain from 6 to 6 UTC on 1975-07-01
    answer = await measurement_service_fixture.read_history("Zurich", date(1975, 7, 1), 1)

    day = answer["weather"]["days"][0]
    assert (day["temperature_mean_c"], day["sunshine_h"]) == (9.8, 1.5)
    assert day["strongest_gust_kmh"] is None
    assert day["rain_06_to_06_utc_mm"] == 9.5
    assert "rain_mm" not in day


@pytest.mark.asyncio
async def test_read_history_only_the_weather(source_fixture, measurement_service_fixture):
    answer = await measurement_service_fixture.read_history("Zurich", date(2026, 9, 23), 2, "weather")

    assert list(answer) == ["place", "weather"]
    assert count_downloads(source_fixture, "ogd-nime") == 0


@pytest.mark.asyncio
async def test_read_history_only_the_snow(source_fixture, measurement_service_fixture):
    # The manual site Davos is next to Davos Dorf, so the block does not name it. The 5 cm of new snow of
    # 2025-01-02 show in the snow depth of the next morning.
    answer = await measurement_service_fixture.read_history("Davos Dorf", date(2025, 1, 1), 3, "snow")

    assert answer == {
        "place": "Davos Dorf",
        "snow": {
            "days": [
                {"date": "2025-01-01", "snow_depth_cm": 38.0, "new_snow_cm": 0.0},
                {"date": "2025-01-02", "snow_depth_cm": 37.0, "new_snow_cm": 5.0},
                {"date": "2025-01-03", "snow_depth_cm": 41.0, "new_snow_cm": 0.0},
            ],
        },
    }
    assert count_downloads(source_fixture, "ogd-smn_dav_d_") == 0


@pytest.mark.asyncio
async def test_read_history_without_a_snow_station(measurement_service_fixture):
    # No manual site of the fakes lies within 25 km of Genève by distance and height
    answer = await measurement_service_fixture.read_history("Genève", date(2026, 9, 23), 1)

    assert answer["weather"]["days"][0]["date"] == "2026-09-23"
    assert answer["snow"] is None


@pytest.mark.asyncio
async def test_read_history_only_the_snow_without_a_snow_station(measurement_service_fixture):
    with pytest.raises(CannotAnswerError, match=r"No snow station is close enough to Genève \(376 m\)"):
        await measurement_service_fixture.read_history("Genève", date(2026, 9, 23), 1, "snow")


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [0, 367])
async def test_read_history_refuses_days_out_of_range(measurement_service_fixture, days):
    with pytest.raises(CannotAnswerError, match=f"days must be between 1 and 366, not {days}."):
        await measurement_service_fixture.read_history("Zurich", date(2026, 9, 23), days)


# --- read_snow ---

def move_slf_now(mocker, later: timedelta) -> None:
    datetime_mock = mocker.patch("swiss_weather_mcp.measurements.slf_source.datetime", wraps=datetime)
    datetime_mock.now.return_value = MEASUREMENTS_NOW + later


def lift_distance_limit(mocker) -> None:
    # The fake SLF stations of the Matterhorn are all farther than the limit, so these tests lift it
    mocker.patch("swiss_weather_mcp.measurements.snow.MAX_SNOW_STATION_WEIGHTED_DISTANCE_M", 1e9)


@pytest.mark.asyncio
async def test_read_snow_from_two_stations(mocker, measurement_service_fixture):
    # Without the distance limit, Fluntern suits Zürich best, but only SLF stations have new snow
    lift_distance_limit(mocker)
    answer = await measurement_service_fixture.read_snow("Zurich")

    assert answer == {
        "place": "Zürich",
        "snow_depth": {
            "data_from": (
                "The snow depth for Zürich (418 m) comes from the MeteoSwiss station Zürich / Fluntern (604 m), "
                "2.1 km away and 186 m higher. It was measured at 16:00 on 2026-09-25."
            ),
            "measured_at": "2026-09-25T16:00+02:00",
            "snow_depth_cm": 0.0,
        },
        "new_snow": {
            "data_from": (
                "The new snow for Zürich (418 m) was measured by an observer at Davos Flüelastr. (1560 m), "
                "116.8 km away and 1142 m higher. It covers the 24 hours and the 3 days up to 08:00 on 2026-09-25."
            ),
            "measured_at": "2026-09-25T08:00+02:00",
            "new_snow_24_hours_cm": 6.0,
            "new_snow_3_days_cm": 16.0,
        },
    }


@pytest.mark.asyncio
async def test_read_snow_from_stations_next_to_the_place(measurement_service_fixture):
    # The MeteoSwiss station Davos is 0.7 km away and 6 m lower, the observer 1.0 km away and 40 m lower
    answer = await measurement_service_fixture.read_snow("Seeli", "Davos Dorf")

    assert answer == {
        "place": "Seeli",
        "snow_depth": {"measured_at": "2026-09-25T16:00+02:00", "snow_depth_cm": 12.0},
        "new_snow": {"measured_at": "2026-09-25T08:00+02:00", "new_snow_24_hours_cm": 6.0, "new_snow_3_days_cm": 16.0},
    }


@pytest.mark.asyncio
async def test_read_snow_from_an_automatic_slf_station(mocker, measurement_service_fixture):
    lift_distance_limit(mocker)
    answer = await measurement_service_fixture.read_snow("Matterhorn")

    assert answer["snow_depth"]["data_from"].startswith(
        "The snow depth for Matterhorn (4478 m) comes from the automatic SLF station Gornergratsee (2953 m)"
    )
    assert answer["snow_depth"]["snow_depth_cm"] == 21.4
    assert answer["new_snow"]["data_from"].startswith(
        "The new snow for Matterhorn (4478 m) comes from the automatic SLF station Gornergratsee (2953 m)"
    )


@pytest.mark.asyncio
async def test_read_snow_without_snow(measurement_service_fixture):
    # 0 cm is a value: there is no snow at Fluntern
    answer = await measurement_service_fixture.read_snow("Zurich")
    assert answer["snow_depth"]["snow_depth_cm"] == 0.0


@pytest.mark.asyncio
async def test_read_snow_leaves_out_stations_too_far_away(measurement_service_fixture):
    # The nearest new snow for Zürich is the observer in Davos, 117 km away and 1142 m higher
    answer = await measurement_service_fixture.read_snow("Zurich")
    assert answer["new_snow"] is None


@pytest.mark.asyncio
async def test_read_snow_without_a_station_close_enough(measurement_service_fixture):
    with pytest.raises(CannotAnswerError) as raised:
        await measurement_service_fixture.read_snow("Matterhorn")
    assert str(raised.value) == (
        "No snow station is close enough to Matterhorn (4478 m) in distance and height. The closest, "
        "Gornergratsee (2953 m), is 9.8 km away and 1525 m lower."
    )


@pytest.mark.asyncio
async def test_read_snow_skips_an_old_snow_depth(mocker, measurement_service_fixture):
    # 3 hours later, the snow depth of Gornergratsee is too old, the one of Triftchumme not yet
    lift_distance_limit(mocker)
    move_slf_now(mocker, timedelta(hours=3))
    answer = await measurement_service_fixture.read_snow("Matterhorn")
    assert "automatic SLF station Triftchumme (2752 m)" in answer["snow_depth"]["data_from"]


@pytest.mark.asyncio
async def test_read_snow_tries_at_most_3_stations(mocker, measurement_service_fixture):
    # 5 hours later, the first 3 stations for the Matterhorn have no current snow depth, and the
    # MeteoSwiss stations, which do, are not tried; the new snow of the morning still counts
    lift_distance_limit(mocker)
    move_slf_now(mocker, timedelta(hours=5))
    answer = await measurement_service_fixture.read_snow("Matterhorn")

    assert answer["snow_depth"] is None
    assert "Gornergratsee" in answer["new_snow"]["data_from"]


@pytest.mark.asyncio
async def test_read_snow_without_current_snow_measurements(mocker, measurement_service_fixture):
    datetime_mock = mocker.patch("swiss_weather_mcp.measurements.meteoswiss_source.datetime", wraps=datetime)
    datetime_mock.now.return_value = MEASUREMENTS_NOW + timedelta(days=3)
    move_slf_now(mocker, timedelta(days=3))

    with pytest.raises(CannotAnswerError) as raised:
        await measurement_service_fixture.read_snow("Seeli", "Davos Dorf")
    assert str(raised.value) == (
        "There are no current snow measurements near Seeli (1600 m): no snow station close enough in distance "
        "and height has a current value. The SLF observers measure the snow from November to April."
    )


@pytest.mark.asyncio
async def test_read_snow_when_slf_cannot_be_reached(mocker, measurement_service_fixture):
    mocker.patch.object(
        measurement_service_fixture.slf_source,
        "list_snow_stations",
        side_effect=requests.ConnectionError("SLF is down"),
    )
    answer = await measurement_service_fixture.read_snow("Zurich")

    assert "MeteoSwiss station Zürich / Fluntern (604 m)" in answer["snow_depth"]["data_from"]
    assert answer["new_snow"] is None


# --- read_extremes ---

@pytest.mark.asyncio
async def test_read_extremes_warmest(measurement_service_fixture):
    # The Uetliberg station has no thermometer, so it is not ranked
    answer = await measurement_service_fixture.read_extremes("warmest")

    assert answer == {
        "extreme": "warmest",
        "measured_at": "2026-09-25T16:00+02:00",
        "stations": [
            {"station": "Zürich / Kloten (426 m)", "canton": "Zurich", "temperature_c": 22.4},
            {"station": "Zürich / Fluntern (604 m)", "canton": "Zurich", "temperature_c": 21.0},
            {"station": "Davos (1594 m)", "canton": "Graubünden", "temperature_c": 16.5},
        ],
    }


@pytest.mark.asyncio
async def test_read_extremes_coldest(measurement_service_fixture):
    answer = await measurement_service_fixture.read_extremes("coldest")
    assert [row["station"] for row in answer["stations"]] == [
        "Davos (1594 m)", "Zürich / Fluntern (604 m)", "Zürich / Kloten (426 m)",
    ]


@pytest.mark.asyncio
async def test_read_extremes_lists_5_stations(mocker, measurement_service_fixture):
    all_measurements = []
    for temperature_c in (11.0, 12.0, 13.0, 14.0, 15.0, 16.0):
        all_measurements.append(build_measurements("2026-09-25T14:00", {parameters.TEMPERATURE: temperature_c}))
    mocker.patch.object(
        measurement_service_fixture.meteoswiss_source, "read_all_stations_latest_measurements",
        return_value=all_measurements,
    )
    answer = await measurement_service_fixture.read_extremes("warmest")
    assert [row["temperature_c"] for row in answer["stations"]] == [16.0, 15.0, 14.0, 13.0, 12.0]


@pytest.mark.asyncio
async def test_read_extremes_windiest(measurement_service_fixture):
    answer = await measurement_service_fixture.read_extremes("windiest")
    assert answer["stations"][0] == {"station": "Davos (1594 m)", "canton": "Graubünden", "gusts_kmh": 28.8}


@pytest.mark.asyncio
async def test_read_extremes_sunniest(measurement_service_fixture):
    # Kloten had only 4 minutes of sun and the other three stations 10, so Graubünden, with Davos at
    # 10 minutes, comes before Zürich, with 8 minutes on average
    answer = await measurement_service_fixture.read_extremes("sunniest")

    no_station = {"rather_sunny": 0, "only_a_little_sunshine": 0, "hardly_any_sunshine": 0}
    assert answer == {
        "extreme": "sunniest",
        "measured_at": "2026-09-25T16:00+02:00",
        "measuring_stations": 4,
        "stations_by_sunshine": {"mostly_sunny": 3, "partly_sunny": 1, **no_station},
        "cantons": [
            {"canton": "Graubünden", "measuring_stations": 1, "mostly_sunny": 1, "partly_sunny": 0, **no_station},
            {"canton": "Zurich", "measuring_stations": 3, "mostly_sunny": 2, "partly_sunny": 1, **no_station},
        ],
    }


@pytest.mark.asyncio
async def test_read_extremes_wettest(measurement_service_fixture):
    # The Uetliberg and Davos stations measure no rain, so they count neither as rainy nor as
    # measuring, and Graubünden has no measuring station left
    answer = await measurement_service_fixture.read_extremes("wettest")

    assert answer == {
        "extreme": "wettest",
        "measured_at": "2026-09-25T16:00+02:00",
        "rainy_stations": 1,
        "measuring_stations": 2,
        "cantons": [{"canton": "Zurich", "rainy_stations": 1, "measuring_stations": 2, "rainy_percent": 50}],
        "stations": [{"station": "Zürich / Kloten (426 m)", "canton": "Zurich", "rain_last_10_minutes_mm": 0.3}],
    }


@pytest.mark.asyncio
async def test_read_extremes_wettest_when_dry(mocker, measurement_service_fixture):
    dry_fluntern = build_measurements("2026-09-25T14:00", {parameters.PRECIPITATION: 0.0})
    mocker.patch.object(
        measurement_service_fixture.meteoswiss_source, "read_all_stations_latest_measurements",
        return_value=[dry_fluntern],
    )
    answer = await measurement_service_fixture.read_extremes("wettest")

    assert (answer["rainy_stations"], answer["measuring_stations"]) == (0, 1)
    # The canton is listed with 0, so it is not mistaken for a canton without measurements
    assert answer["cantons"] == [
        {"canton": "Zurich", "rainy_stations": 0, "measuring_stations": 1, "rainy_percent": 0},
    ]
    assert answer["stations"] == []


@pytest.mark.asyncio
async def test_read_extremes_unknown_extreme(measurement_service_fixture):
    with pytest.raises(CannotAnswerError, match="warmest, coldest, windiest, wettest, sunniest"):
        await measurement_service_fixture.read_extremes("foggiest")


@pytest.mark.asyncio
async def test_read_extremes_without_measurements(mocker, measurement_service_fixture):
    mocker.patch.object(
        measurement_service_fixture.meteoswiss_source, "read_all_stations_latest_measurements", return_value=[],
    )
    with pytest.raises(CannotAnswerError, match="publishes no measurements"):
        await measurement_service_fixture.read_extremes("warmest")
