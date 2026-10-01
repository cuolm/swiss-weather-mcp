import csv
import logging
import time
from importlib import resources

import pytest
import requests

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.locations import MeteoSwissPoint
from swiss_weather_mcp.locations.meteoswiss_points import OTHER_LANGUAGE_PLACE_NAMES_FILE
from swiss_weather_mcp.locations.names import normalise_location


# --- normalise_location ---

def test_normalise_location_ignores_periods():
    # MeteoSwiss writes "St. Moritz", which is often typed without the period
    assert normalise_location("St Moritz") == normalise_location("St. Moritz")
    assert normalise_location("Affoltern i.E.") == normalise_location("Affoltern i. E.")


# --- find_location ---

def test_find_location_ignores_case_and_accents(location_finder_fixture):
    for spelling in ("Zürich", "zurich", "ZURICH"):
        assert location_finder_fixture.find_location(spelling).point.point_id == "800100"


def test_find_location_lowest_postal_code(location_finder_fixture):
    # 8001 is the historic centre, 8002 is a different district of the same city
    assert location_finder_fixture.find_location("Zürich").point.postal_code == "8001"


def test_find_location_accepts_a_postal_code(location_finder_fixture):
    assert location_finder_fixture.find_location("8002").point.point_id == "800200"


def test_find_location_station_without_postal_code(location_finder_fixture):
    point = location_finder_fixture.find_location("Davos").point
    assert (point.point_id, point.point_type_id) == ("26", "1")


def test_find_location_caches_the_point_table_in_its_folder(location_finder_fixture, tmp_path):
    location_finder_fixture.find_location("8001").point
    assert (tmp_path / "locations" / "ogd-local-forecasting_meta_point.csv").exists()


def test_find_location_reads_the_coordinates(location_finder_fixture):
    point = location_finder_fixture.find_location("8001").point
    assert (point.east_m, point.north_m) == (2683348.0, 1247414.0)


def test_find_location_rejects_a_near_match(location_finder_fixture):
    # "Wallis" is the canton Valais, its closest name here is a Zurich suburb 150 km away
    with pytest.raises(CannotAnswerError, match="was not found"):
        location_finder_fixture.find_location("Wallis").point


def test_find_location_unknown_location(location_finder_fixture):
    with pytest.raises(CannotAnswerError, match="'Tessin'"):
        location_finder_fixture.find_location("Tessin").point


def test_find_location_other_language_name(location_finder_fixture):
    # "Zurigo" is the Italian name of Zürich, from other_language_place_names.csv
    assert location_finder_fixture.find_location("Zurigo").point.point_id == "800100"


def test_find_location_during_loading(mocker, location_finder_fixture):
    # A second request can ask for a place while the first is still reading the table. Ask for
    # Davos just after the first place of the table has been read.
    points_built = []
    found_meanwhile = []

    def build_point_and_ask_again(**fields):
        points_built.append(fields["point_id"])
        if len(points_built) == 2:
            found_meanwhile.append(location_finder_fixture.find_location("Davos").point)
        return MeteoSwissPoint(**fields)

    mocker.patch("swiss_weather_mcp.locations.meteoswiss_points.MeteoSwissPoint", side_effect=build_point_and_ask_again)
    location_finder_fixture.find_location("Zürich").point

    assert found_meanwhile[0].point_id == "26"


def count_archive_downloads(source_fixture) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith(".csv.zip"):
            downloads += 1
    return downloads


def test_find_location_meteoswiss_name_comes_first(source_fixture, location_finder_fixture):
    # swisstopo has a Davos too, but the MeteoSwiss point wins, and the swisstopo places are not even read
    resolved_location = location_finder_fixture.find_location("Davos")
    assert (resolved_location.point.point_id, resolved_location.place) == ("26", None)
    assert count_archive_downloads(source_fixture) == 0


def test_find_location_swisstopo_place(location_finder_fixture):
    resolved_location = location_finder_fixture.find_location("Lindenhof")
    assert resolved_location.place.display_name == "Lindenhof (428 m)"
    assert resolved_location.point.point_id == "800100"


def test_find_location_takes_a_settlement_for_close_places(location_finder_fixture):
    # The Uetliberg hill and the hamlet on it lie 360 m apart, so they are one place
    resolved_location = location_finder_fixture.find_location("Uetliberg")
    assert resolved_location.place.object_type == "Ort"


def test_find_location_refuses_a_name_of_several_places(location_finder_fixture):
    # The Blausee place and district lie 280 m apart and are one place
    with pytest.raises(CannotAnswerError) as raised:
        location_finder_fixture.find_location("Blausee")
    assert str(raised.value) == (
        "'Blausee' is the name of 2 places. Call again with location 'Blausee' and near set to one of these:\n"
        '  near="Zürich": place, 420 m\n'
        '  near="Wallisellen": lake, 445 m'
    )


def test_find_location_chooses_the_place_closest_to_near(location_finder_fixture):
    # near need not be a listed village: a postal code in Zürich chooses the Blausee there too
    assert location_finder_fixture.find_location("Blausee", nearby_location="Wallisellen").place.object_type == "See"
    assert location_finder_fixture.find_location("Blausee", nearby_location="8001").place.object_type == "Ort"


def test_find_location_refuses_a_near_far_from_every_place(location_finder_fixture):
    # Davos lies more than 15 km from both Blausee
    with pytest.raises(CannotAnswerError, match="No 'Blausee' lies near 'Davos'. 'Blausee' is the name of 2 places"):
        location_finder_fixture.find_location("Blausee", nearby_location="Davos")


def test_find_location_refuses_an_unknown_near(location_finder_fixture):
    with pytest.raises(CannotAnswerError, match="No 'Blausee' lies near 'Kandertal'"):
        location_finder_fixture.find_location("Blausee", nearby_location="Kandertal")


def test_find_location_two_places_with_the_same_nearest_point(location_finder_fixture):
    # Both Moos list Wallisellen, which chooses the Moos closer to it
    with pytest.raises(CannotAnswerError) as raised:
        location_finder_fixture.find_location("Moos")
    assert '  near="Wallisellen": place, 440 m' in str(raised.value)
    assert '  near="Wallisellen": place, 450 m' in str(raised.value)
    assert location_finder_fixture.find_location("Moos", nearby_location="Wallisellen").place.altitude_m == 440.0


def test_find_location_refuses_a_near_farther_than_10_km(location_finder_fixture):
    with pytest.raises(CannotAnswerError, match="No 'Seeli' lies near 'Wallisellen'"):
        location_finder_fixture.find_location("Seeli", nearby_location="Wallisellen")


def test_find_location_ignores_near_for_a_name_of_one_place(location_finder_fixture):
    resolved_location = location_finder_fixture.find_location("Lindenhof", nearby_location="Davos")
    assert resolved_location.place.name == "Lindenhof"


# --- start_loading_swisstopo_places ---

# Long enough that a request comes while the background load is still downloading
SLOW_DOWNLOAD_SECONDS = 0.2


def test_start_loading_swisstopo_places(source_fixture, location_finder_fixture):
    location_finder_fixture.start_loading_swisstopo_places().join()
    location_finder_fixture.find_location("Lindenhof")
    assert count_archive_downloads(source_fixture) == 1


def test_start_loading_swisstopo_places_while_a_request_needs_them(source_fixture, location_finder_fixture):
    fast_get = source_fixture.get_mock.side_effect

    def slow_get(url, **kwargs):
        if url.endswith(".csv.zip"):
            time.sleep(SLOW_DOWNLOAD_SECONDS)
        return fast_get(url, **kwargs)

    source_fixture.get_mock.side_effect = slow_get
    loading_thread = location_finder_fixture.start_loading_swisstopo_places()
    resolved_location = location_finder_fixture.find_location("Lindenhof")
    loading_thread.join()

    assert resolved_location.place.name == "Lindenhof"
    assert count_archive_downloads(source_fixture) == 1


def test_start_loading_swisstopo_places_without_a_connection(caplog, source_fixture, location_finder_fixture):
    source_fixture.get_mock.side_effect = requests.ConnectionError("no connection")
    with caplog.at_level(logging.WARNING):
        location_finder_fixture.start_loading_swisstopo_places().join()
    assert "Could not load the swisstopo places at start" in caplog.text


# --- other_language_place_names.csv ---

def _read_other_language_place_name_rows():
    """Read the rows of the shipped file as they are, so duplicates are still visible."""
    package_files = resources.files("swiss_weather_mcp.locations")
    names_file = package_files.joinpath(OTHER_LANGUAGE_PLACE_NAMES_FILE)
    text = names_file.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter=";"))


def test_other_language_place_names_file():
    rows = _read_other_language_place_name_rows()

    assert len(rows) > 100
    for row in rows:
        assert all(row.values()), row
    other_language_place_names = [normalise_location(row["other_language_place_name"]) for row in rows]
    assert len(other_language_place_names) == len(set(other_language_place_names)), "a name points to two places"
    point_names = {normalise_location(row["meteoswiss_point_name"]) for row in rows}
    assert not point_names & set(other_language_place_names), "a name would hide a real point name"
