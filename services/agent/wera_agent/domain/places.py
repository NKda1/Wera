"""The countries a search can target, what each is called, and which country a place is in (SRCH-11).

One table, because there used to be three — a filter's aliases, Firecrawl's names, and the web
app's picker — and the picker only offered the countries a *reviewed board* covered. That was
most of Europe's boards' one or two countries, so somebody looking in Toronto or Sydney had no
way to say so, although the open web search, Adzuna and Careerjet all search there.

**Every country GeoNames lists can be picked** (16 Nov: someone looking in Dubai had no way to
say so). Serper's `gl` covers them all and `sources.feeds` says which APIs do. The names and
continents come from `places_data/countries.tsv`; the aliases below are the ways a posting writes
a country that its name alone doesn't cover ("UK", "UAE", "Deutschland").

**A place is resolved to its country with a gazetteer** (`places_data/cities.tsv.gz`: every
place of 15,000 people or more, with the Latin-script names a posting might use). Before it, a
posting that named only its city — "Zürich", "Belfast", "Bedford" — matched no country at all,
and was hidden from a search in that country. Built by `scripts/build_places.py` from GeoNames
(CC BY 4.0).
"""

import gzip
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

DATA = Path(__file__).with_name("places_data")


class Country(NamedTuple):
    name: str
    region: str
    # How a posting writes it, lower case and without accents. The first is the name searches use.
    aliases: tuple[str, ...]


# The ways a posting names a country beyond its GeoNames name. The first entry, when given,
# replaces the GeoNames name as the one searches use ("United Kingdom", not "U.K.").
ALIASES: dict[str, tuple[str, ...]] = {
    "GB": ("united kingdom", "uk", "u.k.", "england", "scotland", "wales", "northern ireland", "britain", "great britain"),
    "IE": ("ireland", "republic of ireland", "eire"),
    "NL": ("netherlands", "the netherlands", "holland"),
    "DE": ("germany", "deutschland"),
    "BE": ("belgium", "belgique", "belgie"),
    "ES": ("spain", "espana"),
    "CH": ("switzerland", "schweiz", "suisse", "svizzera"),
    "CZ": ("czechia", "czech republic"),
    "AT": ("austria", "osterreich"),
    "IT": ("italy", "italia"),
    "PL": ("poland", "polska"),
    "SE": ("sweden", "sverige"),
    "DK": ("denmark", "danmark"),
    "NO": ("norway", "norge"),
    "FI": ("finland", "suomi"),
    "US": ("united states", "usa", "u.s.", "u.s.a.", "us", "united states of america"),
    "MX": ("mexico",),
    "BR": ("brazil", "brasil"),
    "NZ": ("new zealand", "aotearoa"),
    "AE": ("united arab emirates", "uae", "u.a.e.", "emirates"),
    "SA": ("saudi arabia", "ksa", "kingdom of saudi arabia"),
    "KR": ("south korea", "korea", "republic of korea"),
    "HK": ("hong kong", "hong kong sar"),
    "CN": ("china", "prc", "people's republic of china"),
    "TW": ("taiwan",),
    "VN": ("vietnam", "viet nam"),
    "RU": ("russia", "russian federation"),
    "CI": ("ivory coast", "cote d'ivoire"),
    "CD": ("democratic republic of the congo", "dr congo", "drc"),
    "PS": ("palestine", "palestinian territory", "occupied palestinian territory"),
    "TR": ("turkey", "turkiye"),
}

# GeoNames' continents, as the picker groups them. The Americas split at the US–Mexico border, as
# job markets do, and the Gulf and the Levant are their own group rather than "Asia Pacific".
CONTINENT_REGIONS = {"EU": "europe", "AF": "africa", "AS": "apac", "OC": "apac", "SA": "latin_america", "AN": "other"}
NORTH_AMERICA = {"US", "CA", "BM", "GL", "PM"}
MIDDLE_EAST = {"AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB", "IL", "PS", "IQ", "IR", "SY", "YE"}
# GeoNames puts Turkey and Cyprus in Asia; their job markets read as Europe's.
EUROPE = {"TR", "CY"}

# The cities a country-wide search names, where the largest by population aren't where the jobs are.
# Everywhere else `hubs` takes the largest — which is right for most countries.
HUBS: dict[str, tuple[str, ...]] = {
    "GB": ("London", "Manchester", "Birmingham"),
    "US": ("New York", "San Francisco", "Seattle"),
    "CA": ("Toronto", "Vancouver", "Montreal"),
    "DE": ("Berlin", "Munich", "Hamburg"),
    "AE": ("Dubai", "Abu Dhabi"),
    "IN": ("Bengaluru", "Hyderabad", "Pune"),
    "AU": ("Sydney", "Melbourne", "Brisbane"),
    "NL": ("Amsterdam", "Rotterdam", "Eindhoven"),
    "CH": ("Zürich", "Geneva", "Basel"),
}

# Regions inside a country that a posting writes after the city ("San Francisco, CA", "Toronto,
# ON"). Several US state codes are also another country's ISO code — CA, DE, IN, GA, AL — so a
# trailing code is read against the city before it (`country_of`).
US_STATES = frozenset(
    [
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "DC",
    ]
)
CA_PROVINCES = frozenset(["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"])


def fold(text: str) -> str:
    """Lower case without accents, single-spaced: "Zürich" and "zurich" are one name."""
    decomposed = unicodedata.normalize("NFKD", text)
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower().split())


def _countries() -> dict[str, Country]:
    out: dict[str, Country] = {}
    for line in (DATA / "countries.tsv").read_text(encoding="utf-8").splitlines():
        code, name, continent, iso3 = line.split("\t")
        ISO3[code] = iso3
        if code in MIDDLE_EAST:
            region = "middle_east"
        elif code in EUROPE:
            region = "europe"
        elif continent == "NA":
            region = "north_america" if code in NORTH_AMERICA else "latin_america"
        else:
            region = CONTINENT_REGIONS.get(continent, "other")
        extra = ALIASES.get(code, ())
        aliases = tuple(dict.fromkeys([*extra, fold(name)]))
        out[code] = Country(name, region, aliases)
    return out


# ISO 3166-1 alpha-2 → alpha-3, for the APIs that take the longer code (ReliefWeb).
ISO3: dict[str, str] = {}
COUNTRIES: dict[str, Country] = {code: c for code, c in _countries().items() if c.region != "other"}


def country_name(code: str) -> str:
    found = COUNTRIES.get(code.strip().upper())
    return found.name if found else code.strip().upper()


# ---------------------------------------------------------------------------
# The gazetteer
# ---------------------------------------------------------------------------


class Place(NamedTuple):
    country: str
    population: int
    name: str  # as the place writes itself: "Zürich"


@lru_cache(maxsize=1)
def _gazetteer() -> dict[str, list[Place]]:
    """Folded name → every place it can mean, largest first (the file is sorted that way)."""
    out: dict[str, list[Place]] = {}
    with gzip.open(DATA / "cities.tsv.gz", "rt", encoding="utf-8") as f:
        for line in f:
            country, population, name, others = line.rstrip("\n").split("\t")
            place = Place(country, int(population), name)
            for key in (fold(name), *(others.split("|") if others else ())):
                out.setdefault(key, []).append(place)
    return out


# A name a smaller place shares counts only when that place is at least this big next to the
# largest of the name: Birmingham, Alabama is (17% of Birmingham, England); Geneva, Illinois (11%
# of Geneva) and Paris, Texas are not.
SHARED_NAME_SHARE = 0.15


def places_named(name: str) -> list[Place]:
    """The places a city name means, largest first — only those a posting plausibly means."""
    found = _gazetteer().get(fold(name).strip(" .")) or []
    if not found:
        return []
    floor = found[0].population * SHARED_NAME_SHARE
    return [p for p in found if p.population >= floor]


def _alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for code, country in COUNTRIES.items():
        for alias in country.aliases:
            index.setdefault(alias, code)
    return index


ALIAS_INDEX = _alias_index()
# Split a place into its parts: "Zürich, CH", "Dubai - United Arab Emirates", "Remote (UK)".
_PARTS = re.compile(r"\s*(?:,|\s[-–—]\s|\(|\)|/|·)\s*")
_WORD = re.compile(r"[a-z][a-z.'’]*")


def country_of(text: str) -> set[str]:
    """The countries a place is in — usually one, empty when WERA can't tell.

    In order of evidence: a country named outright ("London, England", "Dubai - UAE"); a code
    after the city ("Zürich, CH", "Austin, TX", "Toronto, ON"), read against the city where the
    code is ambiguous (CA is California and Canada); then the city itself, from the gazetteer.
    A code is only read as the last part and in capitals, so the "in" of "Remote in London" is
    never India.
    """
    raw_parts = [p for p in _PARTS.split(text.strip()) if p and p.strip()]
    if not raw_parts:
        return set()
    folded = [fold(p) for p in raw_parts]

    named = {ALIAS_INDEX[p] for p in folded if p in ALIAS_INDEX}
    if not named:
        # A country inside a longer part: "Remote - USA" has split already; "London England" hasn't.
        for part in folded:
            words = _WORD.findall(part)
            for size in (3, 2, 1):
                for i in range(len(words) - size + 1):
                    phrase = " ".join(words[i : i + size])
                    # Two letters only for "uk": "us" is also "contact us", "in" is "in".
                    if phrase in ALIAS_INDEX and (len(phrase) > 2 or phrase == "uk"):
                        named.add(ALIAS_INDEX[phrase])
    if named:
        return named

    city = _city_countries(raw_parts)
    last = raw_parts[-1].strip()
    if len(raw_parts) > 1 and re.fullmatch(r"[A-Z]{2,3}", last):
        options = set()
        if last in COUNTRIES:
            options.add(last)
        if last in US_STATES:
            options.add("US")
        if last in CA_PROVINCES:
            options.add("CA")
        iso3 = _ISO3.get(last)
        if iso3:
            options.add(iso3)
        if options:
            everywhere = {p.country for part in raw_parts[:-1] for p in _gazetteer().get(fold(part), [])}
            return (options & everywhere) or (options & city) or options
    return city


def _city_countries(parts: list[str]) -> set[str]:
    """The countries the first part that names a city is in. A part that names nothing as a
    whole is tried by its leading words ("Oxford Business Park North" is Oxford), then by its
    last ("Remote in London" is London)."""
    for part in parts:
        words = fold(part).split()
        for size in range(len(words), 0, -1):
            for found in (places_named(" ".join(words[:size])), places_named(" ".join(words[-size:]))):
                if found:
                    return {p.country for p in found}
    return set()


def hubs(country: str, n: int = 3) -> list[str]:
    """The cities a country-wide search names: where the jobs are, else the largest places."""
    code = country.upper()
    if code in HUBS:
        return list(HUBS[code][:n])
    seen: list[str] = []
    for places in _largest():
        if places.country == code and places.name not in seen:
            seen.append(places.name)
            if len(seen) == n:
                break
    return seen


@lru_cache(maxsize=1)
def _largest() -> list[Place]:
    out: list[Place] = []
    with gzip.open(DATA / "cities.tsv.gz", "rt", encoding="utf-8") as f:
        for line in f:
            country, population, name, _ = line.rstrip("\n").split("\t")
            out.append(Place(country, int(population), name))
    return out


def split_places(raw: str) -> list[str]:
    """A posting's location as the places it names: "San Francisco, CA | New York, NY" is two,
    and so is "Cardiff, London or Remote (UK)"."""
    return [p.strip() for p in re.split(r"\s*(?:\||;|•|\s+or\s+|\s+&\s+)\s*", raw) if p.strip()]


# ISO 3166-1 alpha-3 codes a posting writes ("London, England, GBR") → alpha-2.
_ISO3 = {three: two for two, three in ISO3.items()}


# ---------------------------------------------------------------------------
# Where a profile looks
# ---------------------------------------------------------------------------

# Words a person puts among their cities that aren't a city: "Remote" is a working pattern.
_NOT_A_CITY = {"remote", "hybrid", "anywhere", "flexible", "worldwide", "global", "home", "home based"}


class Where(NamedTuple):
    """A profile's countries and cities, as the search and the filter both read them."""

    # Every country the search targets: the ones ticked, then any a named city is in.
    countries: list[str]
    # Country → the cities named in it, as the person wrote them ("London", not "London, UK").
    cities: dict[str, list[str]]
    # Cities WERA couldn't place in any country. They count in every target country.
    loose: list[str]


# The North American market is these two (17 Nov); Bermuda, Greenland and Saint Pierre are in the
# region but are not job markets anyone asked for.
NORTH_AMERICA_MARKET = {"US", "CA"}


def market_countries() -> set[str]:
    """The countries WERA searches for now: every country in `markets.regions`."""
    from ..config import wera

    regions = set(wera().markets.regions)
    return {
        code
        for code, country in COUNTRIES.items()
        if country.region in regions and (country.region != "north_america" or code in NORTH_AMERICA_MARKET)
    }


def searched(w: Where) -> Where:
    """`w` without the countries outside the markets, and their cities: what a search may ask.
    The profile keeps them; the filter still reads them (MATCH-01)."""
    markets = market_countries()
    return Where([c for c in w.countries if c in markets], {c: v for c, v in w.cities.items() if c in markets}, w.loose)


def where(countries: list[str], locations: list[str]) -> Where:
    """Read `countries` and `locations` together (SRCH-11).

    A city in a country that isn't ticked adds that country — "Dubai" means the UAE whether or
    not the UAE is ticked. A city shared by places in several countries goes to the one the
    person ticked, else to the largest place of that name (Birmingham, England, over Alabama).
    """
    chosen = [c.strip().upper() for c in countries if c.strip()]
    targets = list(dict.fromkeys(chosen))
    cities: dict[str, list[str]] = {}
    loose: list[str] = []
    for location in locations:
        city = location.split(",")[0].strip()
        if not city or fold(city) in _NOT_A_CITY:
            continue
        found = country_of(location)
        inside = [c for c in targets if c in found]
        if not inside and found:
            largest = next((p.country for p in places_named(city) if p.country in found), None)
            inside = [largest or sorted(found)[0]]
            targets += [c for c in inside if c not in targets]
        if not inside:
            loose.append(city)
            continue
        for code in inside:
            cities.setdefault(code, [])
            if city not in cities[code]:
                cities[code].append(city)
    return Where(targets, cities, loose)
