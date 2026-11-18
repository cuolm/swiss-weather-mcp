"""The Swiss cantons by their abbreviation, with their names in the local language."""

# The station tables of MeteoSwiss and SLF name a canton by its abbreviation, and list Liechtenstein like a
# canton
LIECHTENSTEIN_ABBREVIATION = "FL"
# The names of swissBOUNDARIES3D, in the local language, so that the stations and the places name a canton alike
CANTON_ABBREVIATION_TO_NAME = {
    "AG": "Aargau", "AI": "Appenzell Innerrhoden", "AR": "Appenzell Ausserrhoden", "BE": "Bern",
    "BL": "Basel-Landschaft", "BS": "Basel-Stadt", "FR": "Fribourg", "GE": "Genève", "GL": "Glarus",
    "GR": "Graubünden", "JU": "Jura", "LU": "Luzern", "NE": "Neuchâtel", "NW": "Nidwalden",
    "OW": "Obwalden", "SG": "St. Gallen", "SH": "Schaffhausen", "SO": "Solothurn", "SZ": "Schwyz",
    "TG": "Thurgau", "TI": "Ticino", "UR": "Uri", "VD": "Vaud", "VS": "Valais", "ZG": "Zug",
    "ZH": "Zürich", LIECHTENSTEIN_ABBREVIATION: "Liechtenstein",
}
# The 26 cantons, without Liechtenstein
SWISS_CANTON_ABBREVIATIONS = tuple(
    abbreviation for abbreviation in CANTON_ABBREVIATION_TO_NAME if abbreviation != LIECHTENSTEIN_ABBREVIATION
)


def find_canton_name(abbreviation: str) -> str:
    """Find the full name of a canton, such as "Zürich" for "ZH"; an unknown abbreviation stays as it is."""
    return CANTON_ABBREVIATION_TO_NAME.get(abbreviation, abbreviation)
