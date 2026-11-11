from datetime import datetime, timezone

from swiss_weather_mcp.avalanche.source import (
    DangerRating, Elevation, _convert_html_to_text, _parse_problem,
)
from swiss_weather_mcp.places.place_source import SwisstopoPlace


def build_place(name: str, altitude_m: float, east_m: float, north_m: float) -> SwisstopoPlace:
    return SwisstopoPlace(
        name=name, object_types=("Ort",), population_band=4, is_exonym=False, altitude_m=altitude_m,
        east_m=east_m, north_m=north_m,
    )


BRAUNWALD = build_place("Braunwald", 1290.0, 2718807.0, 1199989.0)
FLIMS_DORF = build_place("Flims Dorf", 1079.0, 2740808.0, 1188878.0)
DAVOS_DORF = build_place("Davos Dorf", 1564.0, 2783248.0, 1187061.0)
MATTERHORN = build_place("Matterhorn", 4478.0, 2617049.0, 1091673.0)
ZURICH = build_place("Zürich", 418.0, 2683500.0, 1247184.0)


def count_bulletin_downloads(source_fixture) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if "/bulletin/" in call.args[0]:
            downloads += 1
    return downloads


# --- _convert_html_to_text ---

def test_convert_html_to_text():
    html_text = (
        "<h1>Weather forecast to Saturday</h1><p>Very  cloudy,\n at times &lt;5 cm.</p>"
        "<h2 contenteditable=\"false\">Fresh snow</h2><ul><li>Simplon area: 5 to 10 cm</li><li>Elsewhere less</li></ul>"
    )
    assert _convert_html_to_text(html_text) == (
        "Weather forecast to Saturday\nVery cloudy, at times <5 cm.\nFresh snow\n- Simplon area: 5 to 10 cm\n"
        "- Elsewhere less"
    )


def test_convert_html_to_text_with_line_breaks():
    # The problem descriptions are plain text with <br/> between their parts
    assert _convert_html_to_text("Large size possible. <br/> Caution is needed.") == (
        "Large size possible.\nCaution is needed."
    )


# --- _parse_problem ---

def test_parse_problem_on_the_whole_mountain():
    # V4 writes the whole mountain as an empty elevation, as in 11 problems of the 2025/26 season; the comment is made up
    problem = _parse_problem({
        "problemType": "gliding_snow",
        "comment": "Glide-snow avalanches are possible.",
        "dangerRatingValue": "moderate",
        "validTimePeriod": "all_day",
        "elevation": {},
        "customData": {"CH": {"coreZoneText": "Danger level \"moderate\" (2) at all altitudes."}},
    })
    assert problem.elevation == Elevation(None, None)
    assert problem.aspects == []
    assert problem.danger_rating == DangerRating("moderate", None, "all_day")


# --- read_bulletins ---

def test_read_bulletins(bulletin_source_fixture):
    bulletin = bulletin_source_fixture.read_bulletins()[0]

    assert bulletin.published_at == datetime(2026, 2, 14, 7, tzinfo=timezone.utc)
    assert bulletin.valid_from == datetime(2026, 2, 14, 7, tzinfo=timezone.utc)
    assert bulletin.valid_until == datetime(2026, 2, 14, 16, tzinfo=timezone.utc)
    assert bulletin.next_update == datetime(2026, 2, 14, 16, tzinfo=timezone.utc)
    assert bulletin.region_names == ["Southern Glarus-Grosstal", "Engelberg", "Calanda"]
    assert bulletin.danger_ratings == [DangerRating("considerable", "neutral", "all_day")]
    assert bulletin.snowpack == "Snowpack\n- Prealps: newer snowdrift accumulations lie on a mostly favourable snowpack."
    assert bulletin.outlook == "Outlook\nSunday\nThe avalanche danger will continue to decrease in the west."
    assert bulletin.weather_forecast == (
        "Weather forecast to Saturday\nOn Saturday, it will be very cloudy.\nFresh snow\n"
        "- Simplon area, western Ticino: 5 to 10 cm"
    )
    assert bulletin.weather_review == "Weather review for Friday\nA little snow fell in the north above 1000 m during the night."


def test_read_bulletins_reads_the_problems(bulletin_source_fixture):
    problem = bulletin_source_fixture.read_bulletins()[0].problems[0]

    assert problem.problem_type == "wind_slab"
    assert problem.danger_rating == DangerRating("considerable", "neutral", "all_day")
    assert problem.elevation == Elevation(1800, None)
    assert problem.aspects == ["N", "NE", "E", "SE", "W", "NW"]
    assert problem.summary == (
        "Danger level \"considerable\" (3=) in west to northeast to southeast facing aspects above 1800m."
    )
    assert problem.description == (
        "Avalanches can be released, even by a single winter sport participant.\n"
        "Backcountry touring calls for experience in the assessment of avalanche danger and caution."
    )


def test_read_bulletins_without_the_values_slf_leaves_out(bulletin_source_fixture):
    # The wet snow of the Davos bulletin has no subdivision, heights, aspects or summary, and the bulletin no weather
    bulletin = bulletin_source_fixture.read_bulletins()[1]

    assert bulletin.danger_ratings == [DangerRating("low", None, "all_day"), DangerRating("moderate", None, "later")]
    wet_snow = bulletin.problems[1]
    assert (wet_snow.elevation, wet_snow.aspects, wet_snow.summary) == (None, [], None)
    assert (bulletin.weather_forecast, bulletin.weather_review) == (None, None)


def test_read_bulletins_when_slf_has_none(mocker, bulletin_source_fixture):
    mocker.patch("fakes.AVALANCHE_BULLETINS", ())
    assert bulletin_source_fixture.read_bulletins() == []


def test_read_bulletins_downloads_once(source_fixture, bulletin_source_fixture):
    bulletin_source_fixture.read_bulletins()
    bulletin_source_fixture.read_bulletins()
    assert count_bulletin_downloads(source_fixture) == 1


# --- AvalancheBulletin.covers ---

def test_covers_a_place_inside_the_outline(bulletin_source_fixture):
    bulletins = bulletin_source_fixture.read_bulletins()
    assert bulletins[0].covers(BRAUNWALD)
    assert not bulletins[1].covers(BRAUNWALD)


def test_covers_not_a_place_in_a_hole(bulletin_source_fixture):
    # Flims Dorf lies inside the outer ring of the Braunwald bulletin, but in its hole
    bulletins = bulletin_source_fixture.read_bulletins()
    assert not bulletins[0].covers(FLIMS_DORF)


def test_covers_each_part_of_a_multipolygon(bulletin_source_fixture):
    bulletins = bulletin_source_fixture.read_bulletins()
    assert bulletins[1].covers(DAVOS_DORF)
    assert bulletins[1].covers(MATTERHORN)


def test_covers_not_the_plateau(bulletin_source_fixture):
    for bulletin in bulletin_source_fixture.read_bulletins():
        assert not bulletin.covers(ZURICH)
