"""Which Ausbildung occupations and places to search for.

`accepts` works on the official occupation name the Arbeitsagentur attaches to each
listing (e.g. "Kraftfahrzeugmechatroniker/in - Personenkraftwagentechnik"), so car
repair listings are dropped even though they match the search word "Mechatroniker".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Occupation:
    key: str
    label: str
    search_terms: Tuple[str, ...]
    exact: Tuple[str, ...] = ()
    prefixes: Tuple[str, ...] = ()
    # Listing titles containing one of these are skipped even if the occupation matches.
    title_blocklist: Tuple[str, ...] = ()

    def accepts(self, occupation_name: str) -> bool:
        name = (occupation_name or "").strip()
        return name in self.exact or any(name.startswith(p) for p in self.prefixes)


CAR_WORDS = ("kfz", "kraftfahrzeug", "autohaus", "karosserie", "zweirad", "nutzfahrzeug", "werkstatt")

OCCUPATIONS: List[Occupation] = [
    Occupation(
        key="mechatronik",
        label="Mechatroniker/in (industrial, no car repair)",
        search_terms=("Mechatroniker",),
        exact=("Mechatroniker/in",),
        title_blocklist=CAR_WORDS,
    ),
    Occupation(
        key="it",
        label="Fachinformatiker/in",
        search_terms=("Fachinformatiker",),
        prefixes=("Fachinformatiker/in",),
    ),
    Occupation(
        key="produktdesign",
        label="Technische/r Produktdesigner/in",
        search_terms=("Technischer Produktdesigner",),
        prefixes=("Technische/r Produktdesigner/in",),
    ),
    Occupation(
        key="bau",
        label="Bautechnische/r Konstrukteur/in (formerly Bauzeichner/in)",
        search_terms=("Bautechnischer Konstrukteur", "Bauzeichner"),
        prefixes=("Bautechnische/r Konstrukteur/in", "Bauzeichner/in"),
    ),
    Occupation(
        key="systemplaner",
        label="Technische/r Systemplaner/in",
        search_terms=("Technischer Systemplaner",),
        prefixes=("Technische/r Systemplaner/in",),
    ),
]

OCCUPATIONS_BY_KEY = {o.key: o for o in OCCUPATIONS}


@dataclass(frozen=True)
class Place:
    key: str
    label: str
    query: str  # value for the API's "wo" parameter
    whole_state: bool = False


PLACES: List[Place] = [
    Place("bayern", "All of Bavaria", "Bayern", whole_state=True),
    Place("muenchen", "München", "München"),
    Place("nuernberg", "Nürnberg", "Nürnberg"),
    Place("wuerzburg", "Würzburg", "Würzburg"),
    Place("stuttgart", "Stuttgart", "Stuttgart"),
    Place("frankfurt", "Frankfurt am Main", "Frankfurt am Main"),
    Place("berlin", "Berlin", "Berlin"),
    Place("duesseldorf", "Düsseldorf", "Düsseldorf"),
    Place("koeln", "Köln", "Köln"),
]

PLACES_BY_KEY = {p.key: p for p in PLACES}

# Bavaria first, everything else second.
PRIORITY_REGIONS = {"BAYERN": 1}
DEFAULT_PRIORITY = 2


def priority_for_region(region: Optional[str]) -> int:
    return PRIORITY_REGIONS.get((region or "").upper(), DEFAULT_PRIORITY)


def match_listing(occupation: Occupation, main_occupation: str, all_occupations: List[str], title: str) -> Optional[str]:
    """Return the matching occupation name for a listing, or None if it should be skipped."""
    lowered = (title or "").lower()
    if any(word in lowered for word in occupation.title_blocklist):
        return None
    for name in [main_occupation] + list(all_occupations or []):
        if occupation.accepts(name):
            return name
    return None
