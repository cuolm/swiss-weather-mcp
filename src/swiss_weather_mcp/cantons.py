"""The Swiss cantons by their abbreviation, as the station tables, the place names and the answers use them."""

# The station tables of MeteoSwiss and SLF name a canton by its abbreviation, and list Liechtenstein like a
# canton
LIECHTENSTEIN_ABBREVIATION = "FL"
CANTON_ABBREVIATION_TO_NAME = {
    "AG": "Aargau", "AI": "Appenzell Innerrhoden", "AR": "Appenzell Ausserrhoden", "BE": "Bern",
    "BL": "Basel-Landschaft", "BS": "Basel-Stadt", "FR": "Fribourg", "GE": "Geneva", "GL": "Glarus",
    "GR": "Graubünden", "JU": "Jura", "LU": "Lucerne", "NE": "Neuchâtel", "NW": "Nidwalden",
    "OW": "Obwalden", "SG": "St. Gallen", "SH": "Schaffhausen", "SO": "Solothurn", "SZ": "Schwyz",
    "TG": "Thurgau", "TI": "Ticino", "UR": "Uri", "VD": "Vaud", "VS": "Valais", "ZG": "Zug",
    "ZH": "Zurich", LIECHTENSTEIN_ABBREVIATION: "Liechtenstein",
}
# The 26 cantons, without Liechtenstein
SWISS_CANTON_ABBREVIATIONS = tuple(
    abbreviation for abbreviation in CANTON_ABBREVIATION_TO_NAME if abbreviation != LIECHTENSTEIN_ABBREVIATION
)


def find_canton_name(abbreviation: str) -> str:
    """Find the full name of a canton, such as "Zurich" for "ZH"; an unknown abbreviation stays as it is."""
    return CANTON_ABBREVIATION_TO_NAME.get(abbreviation, abbreviation)
