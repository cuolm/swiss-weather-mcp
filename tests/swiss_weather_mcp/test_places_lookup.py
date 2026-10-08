from swiss_weather_mcp.places.lookup import normalise_location


def find_names(place_lookup, location: str):
    return [place.name for place in place_lookup.find_places(location)]


def count_archive_downloads(source_fixture) -> int:
    downloads = 0
    for call in source_fixture.get_mock.call_args_list:
        if call.args[0].endswith(".csv.zip"):
            downloads += 1
    return downloads


# --- normalise_location ---

def test_normalise_location_ignores_periods():
    # swisstopo writes "St. Moritz", which is often typed without the period
    assert normalise_location("St Moritz") == normalise_location("St. Moritz")
    assert normalise_location("Affoltern i.E.") == normalise_location("Affoltern i. E.")


# --- find_places ---

def test_find_places_ignores_case_and_accents(place_lookup_fixture):
    # Zurich, the French name in the same spot, is merged into Zürich, whose official name is shown
    for spelling in ("Zürich", "zurich", "ZURICH"):
        assert find_names(place_lookup_fixture, spelling) == ["Zürich"]


def test_find_places_without_the_canton(place_lookup_fixture):
    # Wil SG is the town, the two districts are smaller, so the town comes first
    places = place_lookup_fixture.find_places("Wil")
    assert [(place.name, place.population_band) for place in places] == [("Wil SG", 7), ("Wil", 6), ("Wil", 4)]
    assert find_names(place_lookup_fixture, "Wil SG") == ["Wil SG"]


def test_find_places_by_each_part_of_a_settlement_name(place_lookup_fixture):
    assert find_names(place_lookup_fixture, "Biel") == ["Biel/Bienne"]
    assert find_names(place_lookup_fixture, "Bienne") == ["Biel/Bienne"]


def test_find_places_keeps_other_names_with_a_slash_whole(place_lookup_fixture):
    # The distances of a shooting range are no names of their own
    assert find_names(place_lookup_fixture, "25m") == []
    assert find_names(place_lookup_fixture, "Küsnacht Im Holletsmoos 300/50/25m") == ["Küsnacht Im Holletsmoos 300/50/25m"]


def test_find_places_merges_close_rows(place_lookup_fixture):
    # The Uetliberg hill and the hamlet on it lie 360 m apart, so they are one place, where the hamlet is
    uetliberg = place_lookup_fixture.find_places("Uetliberg")
    assert [(place.object_types, place.altitude_m) for place in uetliberg] == [(("Ort", "Huegel"), 842.0)]


def test_find_places_biggest_first(place_lookup_fixture):
    # The Blausee place and district are one place with people, which comes before the lake
    blausee = place_lookup_fixture.find_places("Blausee")
    assert [place.object_types for place in blausee] == [("Ort", "Quartier"), ("See",)]


def test_find_places_skips_field_names(place_lookup_fixture):
    assert place_lookup_fixture.find_places("Rüti") == []


def test_find_places_reads_the_archive_once(source_fixture, place_lookup_fixture):
    place_lookup_fixture.find_places("Zürich")
    place_lookup_fixture.find_places("Wil")
    assert count_archive_downloads(source_fixture) == 1


# --- find_similar_names ---

def test_find_similar_names_starting_with_the_name(place_lookup_fixture):
    # No row is called Davos, so the places whose name starts with it are suggested
    assert [place.name for place in place_lookup_fixture.find_similar_names("Davos")] == ["Davos Dorf", "Davos Platz"]


def test_find_similar_names_spelled_alike(place_lookup_fixture):
    assert place_lookup_fixture.find_similar_names("Zuerich")[0].name == "Zürich"


def test_find_similar_names_starting_with_the_name_first(place_lookup_fixture):
    # Flums is bigger, but Flims Dorf starts with the name the user typed
    assert [place.name for place in place_lookup_fixture.find_similar_names("Flims")] == ["Flims Dorf", "Flums"]


def test_find_similar_names_without_any(place_lookup_fixture):
    assert place_lookup_fixture.find_similar_names("Tessin") == []


# --- near_settlements ---

def test_near_settlements(place_lookup_fixture):
    names = {place.name for place in place_lookup_fixture.near_settlements}
    assert {"Wil SG", "Zürich", "Genève", "Davos Dorf"} <= names
    # Blausee, Wil and Moos are names of several places, Uetliberg has fewer than 100 people,
    # and Genf is the German name of Genève
    assert not {"Blausee", "Wil", "Moos", "Uetliberg", "Genf"} & names
