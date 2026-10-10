"""Company name normalisation, shared by search import and website matching."""
from __future__ import annotations

import re
from typing import List

UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a"})

# Legal forms and filler words that never belong in a domain or a name comparison.
LEGAL_WORDS = {
    "gmbh", "mbh", "ag", "kg", "kgaa", "ohg", "gbr", "ug", "se", "ev", "e", "v", "ek", "eg",
    "co", "cokg", "haftungsbeschraenkt", "und", "the", "inc", "ltd", "llc", "partg", "partgmbb",
    "gesellschaft", "fuer", "mit", "beschraenkter", "haftung", "holding", "gruppe", "group",
}


def fold(text: str) -> str:
    """Lowercase, transliterate umlauts, turn every non-alphanumeric run into one space."""
    text = (text or "").lower().translate(UMLAUTS)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def name_tokens(name: str) -> List[str]:
    return [t for t in fold(name).split() if t not in LEGAL_WORDS]


def company_key(name: str) -> str:
    """Key used to merge listings of the same employer."""
    tokens = name_tokens(name)
    return " ".join(tokens) if tokens else fold(name)
