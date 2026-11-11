import logging
import threading
import time

import pytest
import requests

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.places.finder import _find_obvious_place
from swiss_weather_mcp.places.place_source import SwisstopoPlace

# Long enough that a request comes while the background load is still downloading
SLOW_DOWNLOAD_SECONDS = 0.2
# A held back download goes on after this at the latest, so a failing test does not hang
HELD_BACK_DOWNLOAD_MAX_SECONDS = 5


def build_place(object_type: str, population_band: int) -> SwisstopoPlace:
    return SwisstopoPlace(
        name="Krone", object_types=(object_type,), population_band=population_band, is_exonym=False,
        altitude_m=500.0, east_m=2600000.0, north_m=1200000.0,
    )


def count_archive_downloads(source_fixture) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith(".csv.zip"):
            downloads += 1
    return downloads


# --- find_place ---

def test_find_place_of_one_name(place_finder_fixture):
    lindenhof = place_finder_fixture.find_place("Lindenhof")
    assert (lindenhof.name, lindenhof.altitude_m) == ("Lindenhof", 428.0)


def test_find_place_ignores_near_for_a_name_of_one_place(place_finder_fixture):
    assert place_finder_fixture.find_place("Lindenhof", nearby_location="Davos Dorf").name == "Lindenhof"


def test_find_place_refuses_a_name_of_several_places(place_finder_fixture):
    # Each place is listed with the nearest settlement that leads back to it
    with pytest.raises(CannotAnswerError) as raised:
        place_finder_fixture.find_place("Blausee")
    assert str(raised.value) == (
        "'Blausee' is the name of 2 places. Call again with location 'Blausee' and near set to the village next "
        "to the place the user means, or ask the user which one they mean. The biggest first:\n"
        '  location="Blausee", near="Lindenhof": settlement and district, 20 to 49 people, 425 m\n'
        '  location="Blausee", near="Oerlikon": lake, 445 m'
    )


def test_find_place_names_the_area_of_each_place(place_finder_fixture):
    for loading_thread in place_finder_fixture.start_loading_places():
        loading_thread.join()
    with pytest.raises(CannotAnswerError) as raised:
        place_finder_fixture.find_place("Blausee")
    error_text = str(raised.value)
    option_lines = error_text.splitlines()[1:]
    assert option_lines == [
        '  location="Blausee", near="Lindenhof": settlement and district, 20 to 49 people, 425 m, municipality Zürich, canton Zürich',
        '  location="Blausee", near="Oerlikon": lake, 445 m, canton Zürich',
    ]


def test_find_place_lists_every_place(place_finder_fixture):
    # The town first, and Wil SG names itself, as its full name belongs to it alone
    with pytest.raises(CannotAnswerError) as raised:
        place_finder_fixture.find_place("Wil")
    option_lines = str(raised.value).splitlines()[1:]
    assert len(option_lines) == 3
    assert option_lines[0] == '  location="Wil", near="Wil SG": settlement, 10,000 to 49,999 people, 583 m'
    assert option_lines[1] == '  location="Wil", near="Oerlikon": district, 2,000 to 9,999 people, 440 m'


def test_find_place_takes_the_obvious_place(place_finder_fixture):
    # The village of 2,000 to 9,999 people, not the hamlet of fewer than 20 in canton Luzern
    grindelwald = place_finder_fixture.find_place("Grindelwald")
    assert (grindelwald.population_band, grindelwald.altitude_m) == (6, 1040.0)


def test_find_place_takes_no_obvious_place_when_near_is_far_from_every_place(place_finder_fixture):
    # A near no Grindelwald lies close to may point to the hamlet, so the village is not taken
    with pytest.raises(CannotAnswerError, match="No 'Grindelwald' lies near 'Davos Dorf'"):
        place_finder_fixture.find_place("Grindelwald", nearby_location="Davos Dorf")


def test_find_place_chooses_the_place_closest_to_near(place_finder_fixture):
    # near need not be a listed settlement: Zürich chooses the Blausee next to it too
    assert place_finder_fixture.find_place("Blausee", nearby_location="Oerlikon").object_types == ("See",)
    assert place_finder_fixture.find_place("Blausee", nearby_location="Zürich").object_types == ("Ort", "Quartier")


def test_find_place_refuses_a_near_far_from_every_place(place_finder_fixture):
    with pytest.raises(CannotAnswerError, match="No 'Blausee' lies near 'Davos Dorf'. 'Blausee' is the name of 2 places"):
        place_finder_fixture.find_place("Blausee", nearby_location="Davos Dorf")


def test_find_place_refuses_an_unknown_near(place_finder_fixture):
    with pytest.raises(CannotAnswerError, match="No 'Blausee' lies near 'Kandertal'"):
        place_finder_fixture.find_place("Blausee", nearby_location="Kandertal")


def test_find_place_refuses_a_near_farther_than_10_km(place_finder_fixture):
    # The Seeli near Zürich lies 15.7 km from Oerlikon
    with pytest.raises(CannotAnswerError, match="No 'Seeli' lies near 'Oerlikon'"):
        place_finder_fixture.find_place("Seeli", nearby_location="Oerlikon")


def test_find_place_two_places_with_the_same_nearest_settlement(place_finder_fixture):
    # Oerlikon is nearer to both Moos than any other settlement, and chooses the Moos closer to it
    with pytest.raises(CannotAnswerError) as raised:
        place_finder_fixture.find_place("Moos")
    assert '  location="Moos", near="Oerlikon": settlement, 100 to 999 people, 440 m' in str(raised.value)
    assert '  location="Moos", near="Oerlikon": settlement, 100 to 999 people, 450 m' in str(raised.value)
    assert place_finder_fixture.find_place("Moos", nearby_location="Oerlikon").altitude_m == 450.0


def test_find_place_suggests_similar_names(place_finder_fixture):
    with pytest.raises(CannotAnswerError) as raised:
        place_finder_fixture.find_place("Davos")
    assert str(raised.value) == (
        "Location 'Davos' was not found. Similar names: \"Davos Dorf\" (settlement, 2,000 to 9,999 people, 1564 m), "
        "\"Davos Platz\" (settlement, 2,000 to 9,999 people, 1558 m). Call again with one of these names as location, "
        "or ask the user."
    )


def test_find_place_unknown_name(place_finder_fixture):
    with pytest.raises(CannotAnswerError) as raised:
        place_finder_fixture.find_place("Tessin")
    assert str(raised.value) == "Location 'Tessin' was not found. Check the spelling, or try a nearby town or village."


# --- _find_obvious_place ---

def test_find_obvious_place_with_only_small_settlements_besides():
    town = build_place("Ort", 5)
    assert _find_obvious_place([build_place("Ort", 3), town, build_place("Quartier", 1)]) == town


def test_find_obvious_place_not_beside_a_peak():
    # Like Krone, a district of 2,000 to 9,999 people and a peak of 3188 m
    assert _find_obvious_place([build_place("Quartier", 6), build_place("Gipfel", 0)]) is None


def test_find_obvious_place_not_beside_a_settlement_of_100_people_or_more():
    assert _find_obvious_place([build_place("Ort", 7), build_place("Ort", 4)]) is None


def test_find_obvious_place_not_when_the_biggest_has_fewer_than_1000_people():
    assert _find_obvious_place([build_place("Ort", 4), build_place("Ort", 1)]) is None


# --- start_loading_places ---

def test_start_loading_places(source_fixture, place_finder_fixture):
    for loading_thread in place_finder_fixture.start_loading_places():
        loading_thread.join()
    place_finder_fixture.find_place("Lindenhof")
    assert count_archive_downloads(source_fixture) == 1


def test_start_loading_places_while_a_request_needs_them(source_fixture, place_finder_fixture):
    fast_get = source_fixture.get_mock.side_effect

    def slow_get(url, **kwargs):
        if url.endswith(".csv.zip"):
            time.sleep(SLOW_DOWNLOAD_SECONDS)
        return fast_get(url, **kwargs)

    source_fixture.get_mock.side_effect = slow_get
    loading_threads = place_finder_fixture.start_loading_places()
    lindenhof = place_finder_fixture.find_place("Lindenhof")
    for loading_thread in loading_threads:
        loading_thread.join()

    assert lindenhof.name == "Lindenhof"
    assert count_archive_downloads(source_fixture) == 1


def test_start_loading_places_loads_the_boundaries_without_waiting_for_the_places(source_fixture, place_finder_fixture):
    fast_get = source_fixture.get_mock.side_effect
    places_may_download = threading.Event()

    def held_back_get(url, **kwargs):
        if url.endswith(".csv.zip"):
            places_may_download.wait(HELD_BACK_DOWNLOAD_MAX_SECONDS)
        return fast_get(url, **kwargs)

    source_fixture.get_mock.side_effect = held_back_get
    places_thread, boundaries_thread = place_finder_fixture.start_loading_places()
    boundaries_thread.join()
    # Lindenhof, while the places are still downloading
    area = place_finder_fixture.boundary_source.find_area(2683200, 1247500)
    places_may_download.set()
    places_thread.join()

    assert area is not None
    assert area.description == "municipality Zürich, canton Zürich"


def test_start_loading_places_without_a_connection(caplog, source_fixture, place_finder_fixture):
    source_fixture.get_mock.side_effect = requests.ConnectionError("no connection")
    with caplog.at_level(logging.WARNING):
        for loading_thread in place_finder_fixture.start_loading_places():
            loading_thread.join()
    assert "Could not load the swisstopo places at start" in caplog.text
    assert "Could not load the swisstopo boundaries" in caplog.text
