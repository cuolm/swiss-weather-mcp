from datetime import date

import pytest

from swiss_weather_mcp.astronomy.service import AstronomyService
from swiss_weather_mcp.errors import CannotAnswerError


@pytest.fixture
def astronomy_service_fixture(place_finder_fixture):
    """Return an AstronomyService finding places in the fake swissNAMES3D archive."""
    return AstronomyService(place_finder_fixture)


# --- read_moon ---

@pytest.mark.asyncio
async def test_read_moon_on_a_day_with_a_principal_phase(astronomy_service_fixture):
    # USNO: moonrise 23:07, moonset 13:51, Last Quarter at 21:28 and 55 % lit
    answer = await astronomy_service_fixture.read_moon("Zürich", date(2026, 11, 1))
    assert answer == {
        "place": "Zürich",
        "date": "2026-11-01",
        "moonrise": "2026-11-01T23:07+01:00",
        "moonset": "2026-11-01T13:51+01:00",
        "moon_phase": "Last Quarter",
        "moon_phase_emoji": "🌗",
        "moon_phase_time": "2026-11-01T21:30+01:00",
        "illuminated_percent": 55,
    }


@pytest.mark.asyncio
async def test_read_moon_on_a_day_with_an_intermediate_phase(astronomy_service_fixture):
    answer = await astronomy_service_fixture.read_moon("Zürich", date(2026, 10, 27))
    assert answer["moon_phase"] == "Waning Gibbous"
    assert answer["moon_phase_emoji"] == "🌖"
    assert "moon_phase_time" not in answer


@pytest.mark.asyncio
async def test_read_moon_on_a_day_without_moonrise(astronomy_service_fixture):
    answer = await astronomy_service_fixture.read_moon("Zürich", date(2026, 11, 2))
    assert answer["moonrise"] is None
    assert answer["moonset"] == "2026-11-02T14:16+01:00"


@pytest.mark.asyncio
async def test_read_moon_uses_near(astronomy_service_fixture):
    # Blausee is the name of two places, so without near the finder asks which one
    with pytest.raises(CannotAnswerError, match="is the name of 2 places"):
        await astronomy_service_fixture.read_moon("Blausee", date(2026, 11, 1))
    answer = await astronomy_service_fixture.read_moon("Blausee", date(2026, 11, 1), "Oerlikon")
    assert answer["place"] == "Blausee"


@pytest.mark.asyncio
async def test_read_moon_unknown_location(astronomy_service_fixture):
    with pytest.raises(CannotAnswerError):
        await astronomy_service_fixture.read_moon("Atlantis", date(2026, 11, 1))


# --- read_sun ---

@pytest.mark.asyncio
async def test_read_sun(astronomy_service_fixture):
    # USNO: civil twilight 06:31 to 17:47, sunrise 07:03, sunset 17:15
    answer = await astronomy_service_fixture.read_sun("Zürich", date(2026, 10, 28))
    assert answer == {
        "place": "Zürich",
        "date": "2026-10-28",
        "civil_twilight_begin": "2026-10-28T06:31+01:00",
        "sunrise": "2026-10-28T07:03+01:00",
        "sunset": "2026-10-28T17:15+01:00",
        "civil_twilight_end": "2026-10-28T17:47+01:00",
    }


@pytest.mark.asyncio
async def test_read_sun_uses_near(astronomy_service_fixture):
    answer = await astronomy_service_fixture.read_sun("Blausee", date(2026, 10, 28), "Oerlikon")
    assert answer["place"] == "Blausee"


@pytest.mark.asyncio
async def test_read_sun_unknown_location(astronomy_service_fixture):
    with pytest.raises(CannotAnswerError):
        await astronomy_service_fixture.read_sun("Atlantis", date(2026, 10, 28))
