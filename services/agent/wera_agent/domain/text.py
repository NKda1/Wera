"""Small text helpers shared by the store and the writer (ports of lib/format.ts, lib/urls.ts)."""

import re
from urllib.parse import urlparse


def slug(s: str) -> str:
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", s.lower()))


def title_case(s: str) -> str:
    return re.sub(r"\b\w", lambda m: m.group().upper(), re.sub(r"[-_]+", " ", s), flags=re.ASCII)


# Joining words a name in capitals still writes in lower case once it isn't shouting.
_SMALL_WORDS = frozenset({"of", "and", "the", "for", "de", "du", "la", "le", "von", "van", "der", "da", "di", "y", "&"})


def display_case(s: str, *, brand: bool = False) -> str:
    """A name or company written all in capitals, in ordinary case (20 Nov): "ELEANOR VANCE" →
    "Eleanor Vance", "NATIONAL BANK OF CANADA" → "National Bank of Canada".

    Only text that is *all* capitals changes — "McLean", "deliveroo" and "JPMorgan Chase" are as
    their owners write them. Capital words of two letters, or with no vowel, are acronyms and stay
    ("HSBC UK", "JP Morgan").
    With `brand`, a single capitalised word stays too: "AECOM", "CITI" and "KPMG" are how those
    companies style themselves, and a guess would be wrong more often than right.
    """
    text = " ".join(s.split())
    letters = [ch for ch in text if ch.isalpha()]
    if not letters or text != text.upper():
        return text
    words = text.split(" ")
    if brand and len(words) == 1:
        return text
    out = []
    for i, word in enumerate(words):
        bare = re.sub(r"[^A-Za-z]", "", word)
        if i and word.lower() in _SMALL_WORDS:
            out.append(word.lower())
        elif len(words) > 1 and bare and (len(bare) <= 2 or (len(bare) <= 5 and not re.search(r"[AEIOU]", bare))):
            out.append(word)  # an acronym: "UK", "JP", "HSBC", "LLP" — short, or no vowel to say it with
        else:
            out.append(re.sub(r"(?<=['’])S\b", "s", word.lower().title()))
    return " ".join(out)


def is_linkedin_url(raw: str) -> bool:
    """LinkedIn is never fetched, scraped or automated (SPEC §11.2, ATS-04)."""
    value = raw.strip()
    try:
        host = (urlparse(value if re.match(r"^[a-z]+://", value, re.I) else f"https://{value}").hostname or "").lower()
    except ValueError:
        return bool(re.search(r"linkedin\.com|lnkd\.in", value, re.I))
    return host == "linkedin.com" or host.endswith(".linkedin.com") or host == "lnkd.in"
