import logging

from swiss_weather_mcp.places.lookup import _read_english_place_names, normalise_name


def find_names(place_lookup, location: str):
    return [place.name for place in place_lookup.find_places_named(location)]


def count_archive_downloads(source_fixture) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith(".csv.zip"):
            downloads += 1
    return downloads


# --- normalise_name ---

def test_normalise_name_ignores_periods():
    # swisstopo writes "St. Moritz", which is often typed without the period
    assert normalise_name("St Moritz") == normalise_name("St. Moritz")
    assert normalise_name("Affoltern i.E.") == normalise_name("Affoltern i. E.")


# --- find_places_named ---

def test_find_places_named_ignores_case_and_accents(place_lookup_fixture):
    # Zurich, the French name in the same spot, is merged into Zürich, whose official name is shown
    for spelling in ("Zürich", "zurich", "ZURICH"):
        assert find_names(place_lookup_fixture, spelling) == ["Zürich"]


def test_find_places_named_without_the_canton(place_lookup_fixture):
    # Wil SG is the town, the two districts are smaller, so the town comes first
    places = place_lookup_fixture.find_places_named("Wil")
    assert [(place.name, place.population_band) for place in places] == [("Wil SG", 7), ("Wil", 6), ("Wil", 4)]
    assert find_names(place_lookup_fixture, "Wil SG") == ["Wil SG"]


def test_find_places_named_by_each_part_of_a_settlement_name(place_lookup_fixture):
    assert find_names(place_lookup_fixture, "Biel") == ["Biel/Bienne"]
    assert find_names(place_lookup_fixture, "Bienne") == ["Biel/Bienne"]


def test_find_places_named_keeps_other_names_with_a_slash_whole(place_lookup_fixture):
    # The distances of a shooting range are no names of their own
    assert find_names(place_lookup_fixture, "25m") == []
    assert find_names(place_lookup_fixture, "Küsnacht Im Holletsmoos 300/50/25m") == ["Küsnacht Im Holletsmoos 300/50/25m"]


def test_find_places_named_merges_close_rows(place_lookup_fixture):
    # The Uetliberg hill and the hamlet on it lie 360 m apart, so they are one place, where the hamlet is
    uetliberg = place_lookup_fixture.find_places_named("Uetliberg")
    assert [(place.object_types, place.altitude_m) for place in uetliberg] == [(("Ort", "Huegel"), 842.0)]


def test_find_places_named_biggest_first(place_lookup_fixture):
    # The Blausee place and district are one place with people, which comes before the lake
    blausee = place_lookup_fixture.find_places_named("Blausee")
    assert [place.object_types for place in blausee] == [("Ort", "Quartier"), ("See",)]


def test_find_places_named_skips_field_names(place_lookup_fixture):
    assert place_lookup_fixture.find_places_named("Rüti") == []


def test_find_places_named_reads_the_archive_once(source_fixture, place_lookup_fixture):
    place_lookup_fixture.find_places_named("Zürich")
    place_lookup_fixture.find_places_named("Wil")
    assert count_archive_downloads(source_fixture) == 1


# --- English names ---

def test_find_places_named_by_an_english_name(place_lookup_fixture):
    assert find_names(place_lookup_fixture, "Geneva") == ["Genève"]
    assert find_names(place_lookup_fixture, "Lake Zurich") == ["Zürichsee"]
    assert find_names(place_lookup_fixture, "Rhine Falls") == ["Rheinfall"]


def test_english_name_never_hides_a_name(mocker, place_lookup_fixture):
    mocker.patch("swiss_weather_mcp.places.lookup._read_english_place_names", return_value={"Wil": "Zürich"})
    assert find_names(place_lookup_fixture, "Wil") == ["Wil SG", "Wil", "Wil"]


def test_english_name_of_a_missing_place(mocker, caplog, place_lookup_fixture):
    # The fake archive has no Vierwaldstättersee
    mocker.patch(
        "swiss_weather_mcp.places.lookup._read_english_place_names",
        return_value={"Lake Lucerne": "Vierwaldstättersee"},
    )
    with caplog.at_level(logging.WARNING):
        assert place_lookup_fixture.find_places_named("Lake Lucerne") == []
    assert "the English name Lake Lucerne finds nothing" in caplog.text


def test_english_place_names_file():
    swissnames3d_names_by_english_name = _read_english_place_names()
    assert "Geneva" in swissnames3d_names_by_english_name
    for english_name, swissnames3d_name in swissnames3d_names_by_english_name.items():
        assert english_name and swissnames3d_name
        assert normalise_name(english_name) != normalise_name(swissnames3d_name), english_name


# --- find_places_named_alike ---

def test_find_places_named_alike_starting_with_the_name(place_lookup_fixture):
    # No row is called Davos, so the places whose name starts with it are suggested
    assert [place.name for place in place_lookup_fixture.find_places_named_alike("Davos")] == ["Davos Dorf", "Davos Platz"]


def test_find_places_named_alike_by_spelling(place_lookup_fixture):
    assert place_lookup_fixture.find_places_named_alike("Zuerich")[0].name == "Zürich"


def test_find_places_named_alike_starting_with_the_name_first(place_lookup_fixture):
    # Flums is bigger, but Flims Dorf starts with the name the user typed
    assert [place.name for place in place_lookup_fixture.find_places_named_alike("Flims")] == ["Flims Dorf", "Flums"]


def test_find_places_named_alike_without_any(place_lookup_fixture):
    assert place_lookup_fixture.find_places_named_alike("Tessin") == []


# --- near_settlements ---

def test_near_settlements(place_lookup_fixture):
    names = {place.name for place in place_lookup_fixture.near_settlements}
    assert {"Wil SG", "Zürich", "Genève", "Davos Dorf"} <= names
    # Blausee, Wil and Moos are names of several places, Uetliberg has fewer than 100 people,
    # and Genf is the German name of Genève
    assert not {"Blausee", "Wil", "Moos", "Uetliberg", "Genf"} & names
