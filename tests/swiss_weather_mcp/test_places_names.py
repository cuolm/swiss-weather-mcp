from swiss_weather_mcp.places.names import build_municipality_lookup_names, normalise_name


# --- normalise_name ---

def test_normalise_name_ignores_periods():
    # swisstopo writes "St. Moritz", which is often typed without the period
    assert normalise_name("St Moritz") == normalise_name("St. Moritz")
    assert normalise_name("Affoltern i.E.") == normalise_name("Affoltern i. E.")


# --- build_municipality_lookup_names ---

def test_build_municipality_lookup_names_without_the_canton():
    lookup_names = build_municipality_lookup_names("Saint-Barthélemy (VD)")
    assert lookup_names == ["saint-barthelemy (vd)", "saint-barthelemy"]


def test_build_municipality_lookup_names_of_each_part_of_a_name_with_a_slash():
    assert build_municipality_lookup_names("Albula/Alvra") == ["albula/alvra", "albula", "alvra"]
