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


# --- find_point ---

def test_find_point_ignores_case_and_accents(location_finder_fixture):
    for spelling in ("Zürich", "zurich", "ZURICH"):
        assert location_finder_fixture.find_point(spelling).point_id == "800100"


def test_find_point_lowest_postal_code(location_finder_fixture):
    # 8001 is the historic centre, 8002 is a different district of the same city
    assert location_finder_fixture.find_point("Zürich").postal_code == "8001"


def test_find_point_accepts_a_postal_code(location_finder_fixture):
    assert location_finder_fixture.find_point("8002").point_id == "800200"


def test_find_point_station_without_postal_code(location_finder_fixture):
    point = location_finder_fixture.find_point("Davos")
    assert (point.point_id, point.point_type_id) == ("26", "1")


def test_find_point_caches_the_point_table_in_its_folder(location_finder_fixture, tmp_path):
    location_finder_fixture.find_point("8001")
    assert (tmp_path / "locations" / "ogd-local-forecasting_meta_point.csv").exists()


def test_find_point_reads_the_coordinates(location_finder_fixture):
    point = location_finder_fixture.find_point("8001")
    assert (point.east_m, point.north_m) == (2683348.0, 1247414.0)


def test_find_point_rejects_a_near_match(location_finder_fixture):
    # "Wallis" is the canton Valais, its closest name here is a Zurich suburb 150 km away
    with pytest.raises(CannotAnswerError, match="was not found"):
        location_finder_fixture.find_point("Wallis")


def test_find_point_unknown_location(location_finder_fixture):
    with pytest.raises(CannotAnswerError, match="'Tessin'"):
        location_finder_fixture.find_point("Tessin")


def test_find_point_other_language_name(location_finder_fixture):
    # "Zurigo" is the Italian name of Zürich, from other_language_place_names.csv
    assert location_finder_fixture.find_point("Zurigo").point_id == "800100"


def test_find_point_during_loading(mocker, location_finder_fixture):
    # A second request can ask for a place while the first is still reading the table. Ask for
    # Davos just after the first place of the table has been read.
    points_built = []
    found_meanwhile = []

    def build_point_and_ask_again(**fields):
        points_built.append(fields["point_id"])
        if len(points_built) == 2:
            found_meanwhile.append(location_finder_fixture.find_point("Davos"))
        return MeteoSwissPoint(**fields)

    mocker.patch("swiss_weather_mcp.locations.meteoswiss_points.MeteoSwissPoint", side_effect=build_point_and_ask_again)
    location_finder_fixture.find_point("Zürich")

    assert found_meanwhile[0].point_id == "26"


# --- find_location ---

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
    # The Blausee place and district lie 280 m apart and are one option
    with pytest.raises(CannotAnswerError) as raised:
        location_finder_fixture.find_location("Blausee")
    assert str(raised.value) == (
        "'Blausee' is the name of 2 places. Ask again with one of these, written exactly as shown:\n"
        '  "Blausee (place, 420 m, near Zürich)"\n'
        '  "Blausee (lake, 445 m, near Wallisellen)"'
    )


def test_find_location_takes_an_option(location_finder_fixture):
    resolved_location = location_finder_fixture.find_location("Blausee (lake, 445 m, near Wallisellen)")
    assert resolved_location.place.object_type == "See"


def test_find_location_takes_free_text_that_names_a_village(location_finder_fixture):
    resolved_location = location_finder_fixture.find_location("Blausee near Wallisellen")
    assert resolved_location.place.object_type == "See"


def test_find_location_lists_the_options_for_free_text_without_a_village(location_finder_fixture):
    with pytest.raises(CannotAnswerError, match="'Blausee' is the name of 2 places"):
        location_finder_fixture.find_location("Blausee im Kandertal")


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
