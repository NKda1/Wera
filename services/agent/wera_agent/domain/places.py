"""The countries a search can target, and what each is called (SRCH-11).

One table, because there used to be three — a filter's aliases, Firecrawl's names, and the web
app's picker — and the picker only offered the countries a *reviewed board* covered. That was
most of Europe's boards' one or two countries, so somebody looking in Toronto or Sydney had no
way to say so, although the open web search, Adzuna and Careerjet all search there.

Every country listed is one at least one source can search: Serper's `gl` covers them all, and
`sources.feeds` says which APIs do. A country missing here is still searched as its bare code
(`filters.country_aliases`), which matches almost nothing — so add the country rather than rely
on that.
"""

from typing import NamedTuple


class Country(NamedTuple):
    name: str
    region: str
    # How a posting writes it, lower case. The first is the name searches use.
    aliases: tuple[str, ...]


COUNTRIES: dict[str, Country] = {
    "GB": Country("United Kingdom", "europe", ("united kingdom", "uk", "england", "scotland", "wales", "northern ireland", "britain")),
    "IE": Country("Ireland", "europe", ("ireland", "republic of ireland", "eire")),
    "NL": Country("Netherlands", "europe", ("netherlands", "the netherlands", "holland")),
    "DE": Country("Germany", "europe", ("germany", "deutschland")),
    "FR": Country("France", "europe", ("france",)),
    "BE": Country("Belgium", "europe", ("belgium", "belgique", "belgië")),
    "ES": Country("Spain", "europe", ("spain", "españa")),
    "CH": Country("Switzerland", "europe", ("switzerland", "schweiz", "suisse")),
    "CZ": Country("Czechia", "europe", ("czechia", "czech republic")),
    "AT": Country("Austria", "europe", ("austria", "österreich")),
    "IT": Country("Italy", "europe", ("italy", "italia")),
    "PL": Country("Poland", "europe", ("poland", "polska")),
    "PT": Country("Portugal", "europe", ("portugal",)),
    "SE": Country("Sweden", "europe", ("sweden", "sverige")),
    "US": Country("United States", "north_america", ("united states", "usa", "us", "united states of america")),
    "CA": Country("Canada", "north_america", ("canada",)),
    "MX": Country("Mexico", "latin_america", ("mexico", "méxico")),
    "BR": Country("Brazil", "latin_america", ("brazil", "brasil")),
    "AU": Country("Australia", "apac", ("australia",)),
    "NZ": Country("New Zealand", "apac", ("new zealand", "aotearoa")),
    "SG": Country("Singapore", "apac", ("singapore",)),
    "IN": Country("India", "apac", ("india",)),
    "ZA": Country("South Africa", "africa", ("south africa",)),
}  # fmt: skip


def country_name(code: str) -> str:
    found = COUNTRIES.get(code.strip().upper())
    return found.name if found else code.strip().upper()
