import pytest

from swiss_weather_mcp.locations import ForecastPoint, _normalise_location, _read_other_language_place_name_rows


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


def test_find_point_rejects_a_near_match(location_finder_fixture):
    # "Wallis" is the canton Valais, its closest name here is a Zurich suburb 150 km away
    with pytest.raises(ValueError, match="not one of the 4 places"):
        location_finder_fixture.find_point("Wallis")


def test_find_point_unknown_location(location_finder_fixture):
    with pytest.raises(ValueError, match="'Tessin'"):
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


# --- _read_other_language_place_name_rows ---

def test_read_other_language_place_name_rows():
    rows = _read_other_language_place_name_rows()

    assert len(rows) > 100
    for row in rows:
        assert all(row.values()), row
    other_language_place_names = [_normalise_location(row["other_language_place_name"]) for row in rows]
    assert len(other_language_place_names) == len(set(other_language_place_names)), "a name points to two places"
    point_names = {_normalise_location(row["meteoswiss_point_name"]) for row in rows}
    assert not point_names & set(other_language_place_names), "a name would hide a real point name"
