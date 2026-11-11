import pytest

from swiss_weather_mcp.avalanche.service import (
    FEEDBACK, SOURCE, AvalancheService, _describe_heights, _format_danger_level, build_bulletin_answer,
)
from swiss_weather_mcp.avalanche.source import DangerRating, Elevation
from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.places.place_source import SwisstopoPlace

# Not in the fake swissNAMES3D archive, so its answer is built without the place finder
BRAUNWALD = SwisstopoPlace(
    name="Braunwald", object_types=("Ort", "Haltestelle Bahn"), population_band=4, is_exonym=False,
    altitude_m=1290.0, east_m=2718807.0, north_m=1199989.0,
)


@pytest.fixture
def avalanche_service_fixture(place_finder_fixture, bulletin_source_fixture):
    """Return an AvalancheService finding places in the fake swissNAMES3D archive and reading the fake bulletins."""
    return AvalancheService(place_finder_fixture, bulletin_source_fixture)


# --- _format_danger_level ---

def test_format_danger_level_with_a_subdivision():
    assert _format_danger_level(DangerRating("considerable", "minus", "all_day")) == "considerable (3-)"


def test_format_danger_level_without_a_subdivision():
    assert _format_danger_level(DangerRating("very_high", None, "all_day")) == "very high (5)"


# --- _describe_heights ---

def test_describe_heights():
    assert _describe_heights(Elevation(1800, None)) == "above 1800 m"
    assert _describe_heights(Elevation(None, 2200)) == "below 2200 m"
    assert _describe_heights(Elevation(1800, 2200)) == "between 1800 and 2200 m"
    assert _describe_heights(Elevation(None, None)) == "all heights"
    assert _describe_heights(None) is None


# --- build_bulletin_answer ---

def test_build_bulletin_answer(bulletin_source_fixture):
    # The real bulletin of Braunwald on 2026-02-14 at 08:00, valid until 17:00 Swiss time
    answer = build_bulletin_answer(bulletin_source_fixture, BRAUNWALD)
    assert answer == {
        "place": "Braunwald",
        "place_altitude_m": 1290,
        "regions": ["Southern Glarus-Grosstal", "Engelberg", "Calanda"],
        "published_at": "2026-02-14T08:00+01:00",
        "valid_from": "2026-02-14T08:00+01:00",
        "valid_until": "2026-02-14T17:00+01:00",
        "next_update": "2026-02-14T17:00+01:00",
        "danger_level": "considerable (3=)",
        "avalanche_problems": [
            {
                "problem": "wind slab",
                "danger_level": "considerable (3=)",
                "time_of_day": "all day",
                "heights": "above 1800 m",
                "aspects": ["N", "NE", "E", "SE", "W", "NW"],
                "summary": "Danger level \"considerable\" (3=) in west to northeast to southeast facing aspects above 1800m.",
                "description": (
                    "Avalanches can be released, even by a single winter sport participant.\n"
                    "Backcountry touring calls for experience in the assessment of avalanche danger and caution."
                ),
            },
        ],
        "snowpack": "Snowpack\n- Prealps: newer snowdrift accumulations lie on a mostly favourable snowpack.",
        "outlook": "Outlook\nSunday\nThe avalanche danger will continue to decrease in the west.",
        "weather_forecast": (
            "Weather forecast to Saturday\nOn Saturday, it will be very cloudy.\nFresh snow\n"
            "- Simplon area, western Ticino: 5 to 10 cm"
        ),
        "weather_review": "Weather review for Friday\nA little snow fell in the north above 1000 m during the night.",
        "source": SOURCE,
        "feedback": FEEDBACK,
    }


def test_build_bulletin_answer_when_slf_has_none(mocker, bulletin_source_fixture):
    mocker.patch("fakes.AVALANCHE_BULLETINS", ())
    with pytest.raises(CannotAnswerError, match="SLF has no avalanche bulletin valid now"):
        build_bulletin_answer(bulletin_source_fixture, BRAUNWALD)


# --- read_bulletin ---

@pytest.mark.asyncio
async def test_read_bulletin_with_a_later_danger_level(avalanche_service_fixture):
    # The Davos bulletin of 2026-04-25: low all day, moderate for the wet snow later in the day
    answer = await avalanche_service_fixture.read_bulletin("Davos Dorf")

    assert answer["place"] == "Davos Dorf"
    assert answer["place_altitude_m"] == 1564
    assert answer["danger_level"] == "low (1)"
    assert answer["danger_level_later"] == "moderate (2)"
    assert answer["avalanche_problems"][1] == {
        "problem": "wet snow",
        "danger_level": "moderate (2)",
        "time_of_day": "later",
        "heights": None,
        "aspects": [],
        "summary": None,
        "description": "As the day progresses there will be an increase in the danger of wet avalanches.",
    }
    assert (answer["weather_forecast"], answer["weather_review"]) == (None, None)


@pytest.mark.asyncio
async def test_read_bulletin_outside_every_region(avalanche_service_fixture):
    with pytest.raises(CannotAnswerError, match=r"Zürich \(418 m\) lies in none of the regions"):
        await avalanche_service_fixture.read_bulletin("Zürich")


@pytest.mark.asyncio
async def test_read_bulletin_unknown_location(avalanche_service_fixture):
    with pytest.raises(CannotAnswerError):
        await avalanche_service_fixture.read_bulletin("Atlantis")
