"""
Turn a name into its lookup names: the forms a user may type it in, normalised.

Case, accents and periods do not matter, a canton after the name may be left out, and each part of a
settlement's or municipality's name with a slash counts on its own:

    "St. Moritz"            → "st moritz"
    "Wil SG"                → "wil sg", "wil"                               a place, its canton after a space
    "Saint-Barthélemy (VD)" → "saint-barthelemy (vd)", "saint-barthelemy"   a municipality, its canton in brackets
    "Biel/Bienne"           → "biel/bienne", "biel", "bienne"
"""
import re
import unicodedata

from ..cantons import SWISS_CANTON_ABBREVIATIONS
from .place_source import SETTLEMENT_TYPES, SwisstopoPlace


# A place such as "Wil SG", the canton telling it apart from the other places called Wil
PLACE_CANTON_SUFFIX = re.compile(rf"^(.*\S) (?:{'|'.join(SWISS_CANTON_ABBREVIATIONS)})$")
# A municipality whose name other municipalities have too, such as "Saint-Barthélemy (VD)"
MUNICIPALITY_CANTON_SUFFIX = re.compile(rf"^(.*\S) \((?:{'|'.join(SWISS_CANTON_ABBREVIATIONS)})\)$")


def normalise_name(name: str) -> str:
    """Fold case, strip accents and drop periods, so "zurich" finds "Zürich" and "St Moritz" finds "St. Moritz"."""
    # NFKD splits "ü" into "u" and a separate accent mark, which is dropped here
    casefolded = name.casefold()
    decomposed = unicodedata.normalize("NFKD", casefolded)
    without_accents = ""
    for character in decomposed:
        if not unicodedata.combining(character):
            without_accents += character
    # A period becomes a space, so "i.E." and "i. E." are the same once the words are joined by one space
    words = without_accents.replace(".", " ").split()
    return " ".join(words)


def _build_names_with_and_without_canton(name: str, canton_suffix: re.Pattern[str]) -> list[str]:
    """Normalise a name, and the name without the canton after it, such as "wil sg" and "wil" for "Wil SG"."""
    lookup_names = [normalise_name(name)]
    canton_suffix_match = canton_suffix.match(name)
    if canton_suffix_match:
        name_without_canton = canton_suffix_match.group(1)
        lookup_name_without_canton = normalise_name(name_without_canton)
        lookup_names.append(lookup_name_without_canton)
    return lookup_names


def _split_at_slashes(name: str) -> list[str]:
    """Normalise each part of a name with slashes, such as "biel" and "bienne" for "Biel/Bienne"; none without one."""
    if "/" not in name:
        return []
    lookup_names_of_parts = []
    for part in name.split("/"):
        if part.strip():
            lookup_name_of_part = normalise_name(part)
            lookup_names_of_parts.append(lookup_name_of_part)
    return lookup_names_of_parts


def _remove_repeated(lookup_names: list[str]) -> list[str]:
    """Keep each lookup name once, where it first comes, as two rules can give the same one."""
    return list(dict.fromkeys(lookup_names))


def build_place_lookup_names(row: SwisstopoPlace) -> list[str]:
    """Build every lookup name of a row of swissNAMES3D, such as "wil sg" and "wil" for "Wil SG"."""
    lookup_names = _build_names_with_and_without_canton(row.name, PLACE_CANTON_SUFFIX)
    # Only settlements: other slashes split names such as the shooting range "Küsnacht 300/50/25m"
    if row.object_types[0] in SETTLEMENT_TYPES:
        lookup_names_of_parts = _split_at_slashes(row.name)
        lookup_names.extend(lookup_names_of_parts)
    return _remove_repeated(lookup_names)


def build_municipality_lookup_names(name: str) -> list[str]:
    """
    Build every lookup name of a municipality, such as "davos" for "Davos".

    The canton in brackets may be left out, and each part of a name with a slash counts on its own:
    "saint-barthelemy (vd)" and "saint-barthelemy" for "Saint-Barthélemy (VD)".
    """
    lookup_names = _build_names_with_and_without_canton(name, MUNICIPALITY_CANTON_SUFFIX)
    lookup_names_of_parts = _split_at_slashes(name)
    lookup_names.extend(lookup_names_of_parts)
    return _remove_repeated(lookup_names)
