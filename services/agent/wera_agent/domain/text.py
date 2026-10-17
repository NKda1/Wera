"""Small text helpers shared by the store and the writer (ports of lib/format.ts, lib/urls.ts)."""

import re
from urllib.parse import urlparse


def slug(s: str) -> str:
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", s.lower()))


def title_case(s: str) -> str:
    return re.sub(r"\b\w", lambda m: m.group().upper(), re.sub(r"[-_]+", " ", s), flags=re.ASCII)


def is_linkedin_url(raw: str) -> bool:
    """LinkedIn is never fetched, scraped or automated (SPEC §11.2, ATS-04)."""
    value = raw.strip()
    try:
        host = (urlparse(value if re.match(r"^[a-z]+://", value, re.I) else f"https://{value}").hostname or "").lower()
    except ValueError:
        return bool(re.search(r"linkedin\.com|lnkd\.in", value, re.I))
    return host == "linkedin.com" or host.endswith(".linkedin.com") or host == "lnkd.in"
