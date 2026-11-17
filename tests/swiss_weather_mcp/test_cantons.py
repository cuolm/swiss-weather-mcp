from swiss_weather_mcp.cantons import SWISS_CANTON_ABBREVIATIONS, find_canton_name


# --- find_canton_name ---

def test_find_canton_name():
    # Liechtenstein is listed like a canton, and an abbreviation added later stays as it is
    assert [find_canton_name(abbreviation) for abbreviation in ("GR", "FL", "XX")] == [
        "Graubünden", "Liechtenstein", "XX",
    ]


# --- SWISS_CANTON_ABBREVIATIONS ---

def test_swiss_canton_abbreviations_leave_out_liechtenstein():
    assert len(SWISS_CANTON_ABBREVIATIONS) == 26
    assert "FL" not in SWISS_CANTON_ABBREVIATIONS
