import csv
from importlib import resources

import pytest

from swiss_weather_mcp.errors import CannotAnswerError
from swiss_weather_mcp.locations import OTHER_LANGUAGE_PLACE_NAMES_FILE, ForecastPoint, _normalise_location


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
    with pytest.raises(CannotAnswerError, match="not one of the 4 places"):
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
        return ForecastPoint(**fields)

    mocker.patch("swiss_weather_mcp.locations.ForecastPoint", side_effect=build_point_and_ask_again)
    location_finder_fixture.find_point("Zürich")

    assert found_meanwhile[0].point_id == "26"


# --- other_language_place_names.csv ---

def _read_other_language_place_name_rows():
    """Read the rows of the shipped file as they are, so duplicates are still visible."""
    package_files = resources.files("swiss_weather_mcp")
    names_file = package_files.joinpath(OTHER_LANGUAGE_PLACE_NAMES_FILE)
    text = names_file.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter=";"))


def test_other_language_place_names_file():
    rows = _read_other_language_place_name_rows()

    assert len(rows) > 100
    for row in rows:
        assert all(row.values()), row
    other_language_place_names = [_normalise_location(row["other_language_place_name"]) for row in rows]
    assert len(other_language_place_names) == len(set(other_language_place_names)), "a name points to two places"
    point_names = {_normalise_location(row["meteoswiss_point_name"]) for row in rows}
    assert not point_names & set(other_language_place_names), "a name would hide a real point name"
