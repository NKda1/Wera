"""The Strategist: where this person's next job is advertised, and in what words.

The thinking half of the job-search pipeline (the Harvester, in `sources`, is the doing half).
It reads the candidate's profile and the search profile and returns a target matrix: per target
country, which known boards to ask first, and the target titles as postings there write them.

Two things it is not allowed to do, both enforced rather than asked for:

* **search a board nobody reviewed.** A board it suggests goes to the same queue as discovery's
  (SRCH-12) — `terms_reviewed_on: null`, unsearchable until a person reads its terms (SRCH-04).
  Before that, the domain has to answer over the network (`site_discovery.confirm`), which is
  the free test that a board a model remembers actually exists.
* **widen the search.** Markets are the profile's countries and boards are the profile's enabled
  ones; the matrix only translates within what the person chose (`queries`).

It also owns the first two stages of the search pipeline (SRCH-20): `queries` turns the parsed CV
and the profile into Google searches, and `discover` sends them through Serper, sorts every result
(`sources.postings`), reads each page of results once for its posting links, and queues the
postings in Redis (`sources.targets`) for the Harvester. Nothing here reads a posting or pays
Firecrawl: those are the Harvester's.
"""

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date

import logfire
from sqlalchemy.orm import Session

from ..clock import iso
from ..config import Site, usable_sites
from ..domain import filters, places
from ..domain.dedupe import canonical_url
from ..errors import ApiError
from ..http import host_of, request
from ..llm.gateway import structured
from ..schemas.cv import MasterCV
from ..schemas.cv_profile import CvProfile
from ..schemas.llm_outputs import PROPOSALS_PER_PLAN, StrategistOutput
from ..schemas.profile import SearchProfile
from ..schemas.strategy import LocalTerm, Market, PlannedQuery, TargetMatrix
from ..sources import feeds, site_discovery
from ..sources.postings import classify, posting_links
from ..sources.robots import USER_AGENT, Robots, refusal
from ..sources.serper import Serper
from ..sources.targets import Target, TargetQueue

# What postings in a market are written in, for when the reply leaves `languages` empty. Only the
# countries WERA's boards cover; anywhere else falls back to English, which is a weak default
# but never a wrong query.
COUNTRY_LANGUAGES = {
    "GB": ["en"],
    "IE": ["en"],
    "US": ["en"],
    "NL": ["nl", "en"],
    "DE": ["de", "en"],
    "FR": ["fr", "en"],
    "BE": ["nl", "fr", "en"],
    "ES": ["es", "en"],
    "CH": ["de", "fr", "en"],
    "CZ": ["cs", "en"],
    "AT": ["de", "en"],
    "IT": ["it", "en"],
    "PL": ["pl", "en"],
    "PT": ["pt", "en"],
    "SE": ["sv", "en"],
    "MX": ["es", "en"],
    "BR": ["pt", "en"],
}


def target_countries(profile: SearchProfile) -> list[str]:
    """The countries to plan for: the ones ticked, then any a named city is in (`places.where`).

    An empty `countries` means "no restriction" to the rest of WERA, which a planner can't plan
    for — but a profile reading "London, UK" has said where perfectly clearly, and refusing it
    for not ticking a box as well would be a validation error about nothing. And a city outside
    the ticked countries is a request to search there: "Dubai" adds the UAE.
    """
    return places.where(profile.countries, profile.locations).countries


def candidate_text(cv: MasterCV, profile: CvProfile) -> str:
    """The professional half of the input: facts off the CV, not the CV itself.

    The Strategist needs to know the field, the level and the stack — not every bullet — so it
    gets the rubric's reading (`domain.cv_profile`) plus the roles held. Less to send, and less
    for a model to fixate on a single line of.
    """
    roles = "; ".join(f"{r.role_title} at {r.company_name} ({r.duration})" for r in profile.professional_history)
    return "\n".join(
        line
        for line in [
            f"Headline: {cv.person.headline}" if cv.person.headline else "",
            f"Based in: {cv.person.location}",
            f"Domains: {', '.join(profile.core_competencies.primary_domains)}"
            if profile.core_competencies.primary_domains
            else "",
            f"Skills: {', '.join(profile.core_competencies.technical_stack)}",
            f"Methodologies: {', '.join(profile.core_competencies.methodologies)}"
            if profile.core_competencies.methodologies
            else "",
            f"Roles: {roles}" if roles else "",
            f"Education: {'; '.join(e.degree for e in cv.education)}" if cv.education else "",
        ]
        if line
    )


def profile_text(profile: SearchProfile) -> str:
    return "\n".join(
        [
            f"Target titles: {', '.join(profile.titles)}",
            f"Countries: {', '.join(target_countries(profile))}",
            f"Cities: {', '.join(profile.locations) or 'any'}",
            f"Seniority: {', '.join(profile.seniority) or 'not set'}",
            f"Remote: {profile.remote}",
        ]
    )


def boards_text(sites: list[Site]) -> str:
    def line(s: Site) -> str:
        where = ", ".join(s.countries) or "anywhere"
        sectors = f"; sectors: {', '.join(s.sectors)}" if s.sectors else ""
        state = "" if s.reviewed else " (not yet reviewed)"
        return f"- {s.domain} — {s.name}; serves {where}{sectors}{state}"

    return "\n".join(line(s) for s in sites) or "(none)"


def plan(
    session: Session,
    *,
    cv: MasterCV,
    cv_profile: CvProfile,
    profile: SearchProfile,
    sites: list[Site],
    model: str,
    now: int,
) -> tuple[TargetMatrix, list[tuple[str, str, list[str], str]]]:
    """One `strategist.plan` call, checked, as a matrix plus the boards it would like added.

    The proposals come back as `(domain, name, sectors, country)` and are not written here: a
    plan is a read, and appending to `sites.yaml` is the caller's decision (`Store.refresh_strategy`).
    """
    countries = target_countries(profile)
    if not countries:
        raise ApiError(422, "NO_COUNTRIES", "Pick a country in your search profile, or name one in a location, first.")
    # Every board WERA knows that nobody declined — unreviewed ones too, so a suggestion doesn't
    # duplicate them. Being ranked here searches nothing: `queries` still asks only the
    # reviewed boards the person enabled, in the order this sets.
    known = [s for s in sites if s.declined_on is None]
    out = structured(
        session,
        "strategist.plan",
        StrategistOutput,
        {"candidate": candidate_text(cv, cv_profile), "profile": profile_text(profile), "boards": boards_text(known)},
        context={
            "countries": set(countries),
            "boards": {s.domain.lower(): [c.upper() for c in s.countries] for s in known},
            "titles": profile.titles,
        },
    )
    markets: list[Market] = []
    proposals: list[tuple[str, str, list[str], str]] = []
    for m in out.markets:
        languages = m.languages or COUNTRY_LANGUAGES.get(m.country, ["en"])
        markets.append(
            Market(
                country=m.country,
                languages=languages,
                search_terms=[LocalTerm(term=t.term, language=t.language, title=t.title) for t in m.search_terms],
                boards=m.boards,
                proposed=[],
            )
        )
        for p in m.proposed_boards:
            if p.domain not in {d for d, *_ in proposals}:
                proposals.append((p.domain, p.name, p.sectors, m.country))
    matrix = TargetMatrix(
        field=out.field,
        markets=markets,
        cv_version=cv.version,
        profile_key=profile_key(profile),
        model=model,
        generated_at=iso(now),
    )
    matrix.queries = queries(profile, cv_profile, sites, matrix)
    return matrix, proposals[:PROPOSALS_PER_PLAN]


def verified_proposals(proposals: list[tuple[str, str, list[str], str]], *, field: str, today: date) -> list[Site]:
    """The suggestions that answered over the network, as review-queue entries.

    `confirm` returning `None` — unreachable — is a rejection here, unlike in discovery. Discovery
    found its hosts in search results, so they exist and a failed fetch is a blip; these came
    from a model's memory, and a domain that doesn't answer is most likely one that isn't there.
    """
    outcome = site_discovery.DiscoveryOutcome()
    found: list[Site] = []
    for domain, name, sectors, country in proposals:
        robots = site_discovery.confirm(site_discovery.Candidate(domain=domain), outcome)
        if robots is not True:
            logfire.info("Strategist proposal {domain} dropped: robots check {result}", domain=domain, result=robots)
            continue
        found.append(
            Site(
                domain=domain,
                name=name,
                terms_reviewed_on=None,  # SRCH-04: a person decides
                countries=[country],
                sectors=sectors,
                discovered_on=today,
                matched=f"Strategist: {field}",
                robots_ok=True,
                examples=[f"https://{domain}/"],
            )
        )
    return found


def profile_key(profile: SearchProfile) -> str:
    """The search profile as far as a plan depends on it: what to look for, where, at what level."""
    relevant = [profile.titles, profile.countries, profile.locations, profile.seniority, profile.remote, sorted(profile.sites)]
    return hashlib.sha256(json.dumps(relevant).encode()).hexdigest()[:16]


def is_current(matrix: TargetMatrix, *, cv_version: int, profile: SearchProfile, model: str) -> bool:
    return matrix.cv_version == cv_version and matrix.profile_key == profile_key(profile) and matrix.model == model


# ---------------------------------------------------------------------------
# SRCH-20, stage 1: the queries

# Titles asked about in tier 1. The profile lists titles best first, and three of them across
# every board is already more postings than one search reads (SRCH-26).
TIER_ONE_TITLES = 3
# CV skills paired with a title in tier 2 — the top of the CV's own skills list, in its order.
SKILLS_PER_TITLE = 2
# An open search never asks LinkedIn (SRCH-03): its postings are never fetched, so finding them
# would spend a query on results WERA must throw away.
NO_LINKEDIN = "-site:linkedin.com"
# The word a search adds when the person only wants early-career work and a title doesn't say so
# itself. Measured live: `"Software Engineer" London` came back mostly Senior and Lead roles, 53
# of 61 postings in one tier that the profile then excluded — searches spent on jobs nobody sees.
LEVEL_QUERY = {"intern": "intern", "graduate": "graduate", "junior": "junior", "entry": "entry level"}


def queries(
    profile: SearchProfile,
    cv_profile: CvProfile | None,
    sites: list[Site],
    matrix: TargetMatrix | None = None,
    *,
    preferences: Iterable[str] = (),
) -> list[PlannedQuery]:
    """The searches for this person, best first, in tiers (SRCH-20, stage 1).

    * Tier 1 — the top titles, as an open Google search and as a `site:` search of every reviewed
      board the person enabled that serves the country.
    * Tier 2 — the remaining titles the same way, then the top titles with the CV's top skills,
      and with any `preferences` (the place left for a later "what work do you want" round).
    * Tier 3 — the titles as the Strategist says local boards write them, in their language.

    Each is written as a person would ask for the job (`_phrase`): `"Software Engineer" graduate
    job in London` — the title as a phrase, their level, "job", and the city unquoted. "London, UK"
    as an exact phrase is why 11 of the last 12 Firecrawl searches came back empty: postings say
    "London", "London, England" or nothing at all, and Google's `gl` already says which country.
    """
    titles = [t.strip() for t in profile.titles if t.strip()]
    if not titles:
        return []
    level = _level_word(profile)
    skills = [s for s in (cv_profile.core_competencies.technical_stack if cv_profile else [])[:SKILLS_PER_TITLE] if s.strip()]
    extra = [p.strip() for p in preferences if p.strip()]
    out: list[PlannedQuery] = []
    seen: set[tuple[str, str, str]] = set()

    def add(q: str, *, gl: str, hl: str, source: str, tier: int, title: str, domain: str | None = None) -> None:
        q = " ".join(q.split())
        if (q, gl, hl) in seen:
            return
        seen.add((q, gl, hl))
        out.append(PlannedQuery(q=q, gl=gl, hl=hl, source=source, domain=domain, tier=tier, title=title))

    def both(title: str, term: str, tier: int, lang: str, *, place: list[str], gl: str, boards: list[Site]) -> None:
        """An open search for `term`, and a `site:` search of each board that posts in `lang`."""
        phrase = _phrase(term, level, place, lang)
        add(f"{phrase} {NO_LINKEDIN}", gl=gl, hl=lang, source="open", tier=tier, title=title)
        for board in boards:
            if lang == "en" or not board.languages or lang in board.languages:
                # Postings only, where the board's posting path is known: its search and category
                # pages are most of what Google holds for it, and none of them is a job.
                path = f" inurl:{board.posting_segment}" if board.posting_segment else ""
                add(
                    f"site:{board.domain}{path} {phrase}",
                    gl=gl,
                    hl=lang,
                    source="board",
                    tier=tier,
                    title=title,
                    domain=board.domain,
                )

    wanted = places.where(profile.countries, profile.locations)
    for country in wanted.countries or [""]:
        gl = country.lower()
        hl = _english_or_first(country, matrix)
        boards = [b for b in usable_sites(profile, sites) if not country or b.serves([country])]
        groups = _places(profile, wanted, country)
        # The first group is the cities the person named, else the country; with `first`, the
        # rest of the country follows a tier later, for the top titles (SRCH-11, 16 Nov).
        where = {"place": groups[0], "gl": gl, "boards": boards}

        for title in titles[:TIER_ONE_TITLES]:
            both(title, title, 1, hl, **where)
        for group in groups[1:]:
            for title in titles[:TIER_ONE_TITLES]:
                both(title, title, 2, hl, place=group, gl=gl, boards=boards)
        for title in titles[TIER_ONE_TITLES:]:
            both(title, title, 2, hl, **where)
        for title in titles[:2]:
            for word in [*skills, *extra]:
                add(
                    f"{_phrase(title, level, groups[0], hl, also=word)} {NO_LINKEDIN}",
                    gl=gl,
                    hl=hl,
                    source="open",
                    tier=2,
                    title=title,
                )
        for market in matrix.markets if matrix else []:
            if market.country != country:
                continue
            for term in market.search_terms:
                if term.term.casefold() not in {t.casefold() for t in titles}:
                    both(term.title, term.term, 3, term.language, **where)
    # Tier 4: page 2 of tier 1. Google ranks a query the same way all day, so a second search the
    # same day finds page 1 already saved — measured live, 21 of 61 results on a repeat run. Sent
    # only for queries whose page 1 was rich in postings (`page_two_worth_asking`).
    for q in [q for q in out if q.tier == 1]:
        out.append(q.model_copy(update={"tier": 4, "page": 2}))
    return out


# SRCH-30: how many refusing hosts an open search names with `-site:`. Each is a few characters of
# a query Google caps at 32 words, and the eight cover every refusal seen so far.
MAX_LEFT_OUT = 8


def robots_probes(profile: SearchProfile, sites: list[Site], hint: dict[str, str]) -> dict[str, str]:
    """Host → a posting URL there, for asking each host's robots.txt before a search (SRCH-30).

    The boards this search would query, at their posting path when it is known; then the hosts in
    `unreadable_hint`, which an open search returns often enough to be worth naming. A hint is
    only a host to *ask* about — whether it is left out is the robots.txt's answer, every week.
    """
    out: dict[str, str] = {}
    for board in usable_sites(profile, sites):
        segment = board.posting_segment
        out[board.domain.removeprefix("www.")] = (
            f"https://{board.domain}/{segment}/wera-check" if segment else f"https://{board.domain}/"
        )
    for host, path in hint.items():
        out.setdefault(host.removeprefix("www."), f"https://{host}{path}")
    return out


def leave_out(planned: list[PlannedQuery], refusing: Iterable[str]) -> list[PlannedQuery]:
    """The plan without the hosts whose robots.txt refuses WERA (SRCH-30).

    A `site:` query for a refusing board is dropped: every posting it finds is one the Harvester
    must turn down (Bright Network answers its robots.txt with a 403, so all of them). An open
    search asks Google to leave those hosts out, so its ten results are ten WERA can read.
    Applying it twice changes nothing.
    """
    hosts = sorted({h.removeprefix("www.") for h in refusing})
    if not hosts:
        return planned
    terms = [f"-site:{h}" for h in hosts[:MAX_LEFT_OUT]]
    out: list[PlannedQuery] = []
    for q in planned:
        if q.source == "board":
            domain = (q.domain or "").removeprefix("www.")
            if any(domain == h or domain.endswith(f".{h}") for h in hosts):
                continue
            out.append(q)
            continue
        missing = [t for t in terms if t not in q.q.split()]
        out.append(q.model_copy(update={"q": f"{q.q} {' '.join(missing)}"}) if missing else q)
    return out


def _phrase(term: str, level: str | None, place: list[str], lang: str, *, also: str = "") -> str:
    """One search, written the way a person looking for this job would type it: the title as a
    phrase, their level when the title doesn't say it, what else they asked for, "job", and where —
    `"Software Engineer" graduate job in "London"`.

    Measured live, the bare `"Software Engineer" London` was too weak: Google answered with senior
    roles, other boards' search pages and articles, and three searches surfaced 3, 1 and 2 jobs.
    "job in London" asks for a vacancy in a place rather than a page that mentions both words.

    The city is quoted on its own. Unquoted, Google read "London" as a hint: the first run of
    these searches came back with graduate roles in Warton, Portsmouth, Bristol and Munich, ten of
    them paid for before the location filter hid them. What emptied the old Firecrawl searches was
    quoting the profile's whole `"London, UK"` — a comma and a country no posting writes — not
    quoting the city. A local-language term gets no English words around it.

    `place` is one place (`_places` gives each search its own); it is a list so `["remote"]` and
    no place at all read the same way as a city.
    """
    if place == ["remote"]:
        town = ""
    elif len(place) == 1:
        town = f'"{place[0]}"'
    else:
        town = "(" + " OR ".join(f'"{p}"' for p in place) + ")" if place else ""
    if lang != "en":
        return " ".join(f'"{term}" {also} {town}'.split())
    lvl = level if level and not _has_level(term) else ""
    where = "remote job" if place == ["remote"] else f"job in {town}" if town else "job"
    return " ".join(f'"{term}" {lvl} {also} {where}'.split())


def _level_word(profile: SearchProfile) -> str | None:
    """The level a search should ask for, when every level the person wants is early-career."""
    levels = [lv.lower().strip() for lv in profile.seniority if lv.strip()]
    if not levels or any(lv not in LEVEL_QUERY for lv in levels):
        return None  # wants mid or above too, or said nothing: don't narrow the search
    return LEVEL_QUERY[levels[0]]


def _has_level(title: str) -> bool:
    return bool(set(title.lower().replace("-", " ").split()) & filters.LEVEL_WORDS)


def _places(profile: SearchProfile, wanted: places.Where, country: str) -> list[list[str]]:
    """The place each of this country's searches names, most wanted first — one per search.

    The cities the person named here (and any WERA couldn't place) come first. Then, for a country
    with no city named — or, with `city_scope: first`, after its cities — the cities the
    country's jobs are in (`places.hubs`). Never the country's name: `job in "Switzerland"` came
    back as salary pages, social posts and other countries' boards (16 Nov), and so did one search
    OR-ing Zürich, Geneva, Basel and Switzerland together; `job in "Zürich"` found postings.
    `["remote"]` when remote work is all the person wants.
    """
    if profile.remote == "remote_only":
        return [["remote"]]
    named = [*wanted.cities.get(country, []), *wanted.loose] if country else list(wanted.loose)
    groups = [[city] for city in named]
    if country and (not groups or profile.city_scope == "first"):
        taken = {places.fold(c) for c in named}
        groups += [[hub] for hub in places.hubs(country) if places.fold(hub) not in taken]
    return groups or [[]]


def _english_or_first(country: str, matrix: TargetMatrix | None) -> str:
    languages = next((m.languages for m in (matrix.markets if matrix else []) if m.country == country), None)
    languages = languages or COUNTRY_LANGUAGES.get(country, ["en"])
    return "en" if "en" in languages else languages[0]


# ---------------------------------------------------------------------------
# SRCH-20, stage 2: discovery

# A page of results is read once for its posting links (SRCH-22): plain HTTP, no browser, no
# credits. Bounded per run, because every one is a fetch of somebody else's page.
LISTINGS_PER_RUN = 8
LINKS_PER_LISTING = 15
MAX_LISTING_BYTES = 3_000_000


class Refused(Exception):
    """A results page the site wouldn't serve: a 403, a rate limit, a bot check. Never retried."""


@dataclass
class Discovery:
    """What one discovery pass did, for the run's record and its Logfire span."""

    tiers: list[int] = field(default_factory=list)
    results: int = 0
    kinds: Counter = field(default_factory=Counter)
    # Postings each page-1 query returned, known or new: what decides whether its page 2 is sent.
    postings_by_query: dict[str, int] = field(default_factory=dict)
    listings_read: int = 0
    listing_links: int = 0
    # Hosts that refused a results page this pass. Not asked again until the next run: a board
    # that answers plain HTTP with a bot check (Gradcracker does) says the same to every page.
    refused: set[str] = field(default_factory=set)
    known: int = 0
    blocked: int = 0
    excluded: int = 0
    # Up to a dozen of the titles excluded, for the span: "51 excluded" alone can't say whether the
    # rule is right or too strict.
    excluded_titles: list[str] = field(default_factory=list)
    queued: int = 0
    errors: list[str] = field(default_factory=list)


def discover(
    planned: list[PlannedQuery],
    tiers: Iterable[int],
    *,
    serper: Serper,
    queue: TargetQueue,
    known_urls: set[str],
    sites: list[Site],
    profile: SearchProfile | None = None,
    robots: Robots | None = None,
    fetch: Callable[[str], str | None] | None = None,
) -> Discovery:
    """Send the planned queries in `tiers`, sort what comes back, and queue the postings.

    Only a `posting` is queued (SRCH-21). A `listing` is read once for its posting links, which
    are sorted by the same rule (SRCH-22); `closed` and `other` are counted and dropped. A posting
    WERA already has, one on a board the person declined, or a LinkedIn page never reaches the
    queue — nor does one whose title the profile excludes ("Senior", "Lead"): MATCH-01 would hide
    it after it was read, so reading it would spend a place in the 8–12 on a job nobody sees.
    Postings are queued after each query, so a pass that stops early keeps what it found.
    """
    rules = robots or Robots()
    read = fetch or _listing_html
    known = {canonical_url(u) or u for u in known_urls}
    declined = {s.domain.lower().removeprefix("www.") for s in sites if s.declined_on is not None}
    taken: set[str] = set()
    out = Discovery(tiers=sorted(set(tiers)))
    found: list[Target] = []

    def keep(query: PlannedQuery, url: str, title: str, snippet: str, when: str | None) -> None:
        """Into this query's batch, unless it is known, declined, excluded or already taken."""
        key = canonical_url(url) or url
        host = host_of(url).removeprefix("www.")
        if any(host == d or host.endswith(f".{d}") for d in declined):
            out.blocked += 1
        elif profile is not None and _excluded(title, profile):
            out.excluded += 1
            if len(out.excluded_titles) < 12:
                out.excluded_titles.append(title[:100])
        elif key in known:
            out.known += 1
        elif key not in taken:
            taken.add(key)
            found.append(Target(url, host, query.source, query.q, len(taken) - 1, title, snippet, when))

    with logfire.span("strategist discover", tiers=out.tiers) as span:
        for query in [q for q in planned if q.tier in out.tiers]:
            results = serper.search(query.q, gl=query.gl, hl=query.hl, page=query.page)
            if results is None:
                if serper.stopped:
                    break
                continue
            out.results += len(results)
            postings = 0
            for result in results:
                kind = classify(result.url, title=result.title, sites=sites)
                out.kinds[kind] += 1
                postings += kind == "posting"
                if kind == "posting":
                    keep(query, result.url, result.title, result.snippet, result.date)
                elif kind == "listing" and out.listings_read < LISTINGS_PER_RUN:
                    for url, text in _listing_links(result.url, rules, read, out):
                        keep(query, url, text, "", None)
            if query.page == 1:
                out.postings_by_query[query.q] = postings
                queue.note_postings(query.q, postings)
            out.queued += queue.push(found)
            found.clear()

        out.errors = list(serper.errors)
        span.set_attributes(
            {
                "queries": serper.sent,
                "results": out.results,
                "postings": out.kinds["posting"],
                "listings": out.kinds["listing"],
                "listings_read": out.listings_read,
                "listing_links": out.listing_links,
                "known": out.known,
                "excluded": out.excluded,
                "excluded_titles": out.excluded_titles,
                "refused_hosts": sorted(out.refused),
                "queued": out.queued,
                "serper_credits": serper.credits_used,
                "serper_cached": serper.cached,
                "serper_coalesced": serper.coalesced,
            }
        )
    return out


def _excluded(title: str, profile: SearchProfile) -> bool:
    """MATCH-01's title rules, on the title a search result or a link came with — the ones a job
    would be hidden by once read. A title of a word or two ("Apply", "View job") says nothing about
    the job, so the target-title rule only judges a title with something in it."""
    if filters.excluded_term(title, profile) or filters.too_senior(title, profile):
        return True
    return len(filters.title_tokens(title)) >= 2 and filters.title_mismatch(title, profile) is not None


def _listing_links(url: str, rules: Robots, read: Callable[[str], str | None], out: Discovery) -> list[tuple[str, str]]:
    """A page of results, read once over plain HTTP for the postings it links to.

    A host that refused one page isn't asked for another this pass, and the refusal is never
    retried or worked around — a bot check is the site saying no.
    """
    host = host_of(url).removeprefix("www.")
    if host in out.refused:
        return []
    try:
        allowed = rules.allows(url)
    except ApiError:
        return []
    if not allowed:
        logfire.info("Listing {url} not read: robots.txt disallows it", url=url)
        return []
    out.listings_read += 1
    try:
        html = read(url)
    except Refused as why:
        out.refused.add(host)
        logfire.info("Listing {url} refused ({why}); not asking {host} again this pass", url=url, why=str(why), host=host)
        return []
    links = posting_links(html, url, host_of(url), LINKS_PER_LISTING) if html else []
    if not links:
        # Most often a board that draws its results with JavaScript: plain HTTP sees an empty shell.
        logfire.info("Listing {url} gave no posting links", url=url)
    out.listing_links += len(links)
    return links


def _listing_html(url: str) -> str | None:
    """The page over plain HTTP, or None if unreachable. Raises `Refused` when the site says no."""
    try:
        response = request("GET", url, follow_redirects=True, headers={"user-agent": USER_AGENT})
    except ApiError:
        raise
    except Exception:  # noqa: BLE001 — an unreachable listing is one fewer page of links, nothing more
        return None
    html = response.content[:MAX_LISTING_BYTES].decode(response.encoding or "utf-8", errors="replace")
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    why = refusal(response.status_code, title.group(1) if title else "")
    if why:
        raise Refused(why)
    return html if response.status_code == 200 else None


# ---------------------------------------------------------------------------
# SRCH-31/32: the job APIs, asked before Serper

# A search's searches for snippet leads' originals: their own cap, so they never eat discovery's.
RESOURCE_QUERIES = 10
# And no date filter on them: the lead's date is the API's, and the employer's page may have been
# indexed weeks before it (13 Nov: with discovery's three weeks, 10 lookups found no original).
RESOURCE_WINDOW = ""


@dataclass
class FeedDiscovery:
    """What asking the job APIs found, per API, for the run's counts and Logfire."""

    by_feed: dict[str, dict[str, object]] = field(default_factory=dict)
    queued: int = 0
    errors: list[str] = field(default_factory=list)


def discover_feeds(
    planned: list[PlannedQuery],
    profile: SearchProfile | None,
    *,
    queue: TargetQueue,
    known_urls: set[str],
    sites: list[Site],
    now: int,
    redis: object | None,
    person: feeds.Person | None = None,
    resource: Serper | None = None,
) -> FeedDiscovery:
    """Ask every job API this search can use, all at once, and queue what they found.

    The queries are the plan's titles (tier 1's first), at the person's level, in their city. The
    same rules as `discover` decide what is queued — not known, not LinkedIn, not a title the
    profile excludes, posted in the last 21 days — and at most `max_queued` a search from each,
    so the open web and the reviewed boards still get their share of the 8–12. A full-text API's
    postings are queued to be read from the API; a snippet API's leads are looked for where the
    employer posted them (`feeds.resource`), with `resource` (a Serper client with its own cap).
    """
    from concurrent.futures import ThreadPoolExecutor

    from ..domain.filters import RECENCY_MS
    from ..http import is_denied
    from ..sources.feeds import adzuna, careerjet, reed, reliefweb, usajobs
    from ..sources.feeds import resource as finding
    from ..sources.provider_guard import Guard

    out = FeedDiscovery()
    countries = sorted({q.gl for q in planned if q.gl} or {c.lower() for c in (profile.countries if profile else [])})
    titles = list(dict.fromkeys(q.title for q in sorted(planned, key=lambda q: q.tier)))
    levels = set(profile.seniority if profile else [])
    wanted = places.where(profile.countries, profile.locations) if profile else places.Where([], {}, [])
    scope = profile.city_scope if profile else "first"
    queries = feeds.queries_for(titles, feeds.places_for(wanted, city_scope=scope, countries=countries))
    exclude = sorted(
        {t.removeprefix("-site:") for q in planned if q.source == "open" for t in q.q.split() if t.startswith("-site:")}
    )

    asks: list[tuple[str, feeds.FeedQuery]] = []
    for feed_id, feed in feeds.FEEDS.items():
        why = feeds.skipped(feed_id, countries=set(countries), person=person)
        if why is None and not feed.full_text and resource is None:
            why = "no Serper to find the original postings with"
        out.by_feed[feed_id] = {"skipped": why} if why else {"searches": 0, "leads": 0, "queued": 0, "how": Counter()}
        if why is None:
            # One search per question the API is actually asked: Reed and USAJOBS search the role,
            # so "Graduate" and "Junior Software Engineer" are the same search there (13 Nov).
            graduate = bool(levels & {"graduate", "intern", "entry"})
            sent: set[tuple[object, ...]] = set()
            mine = []
            for q in queries:
                asked_as = (q.role if feed_id == "usajobs" or (feed_id == "reed" and graduate and q.level) else q.title, q.level if feed_id == "adzuna" else None, q.city, q.country)  # fmt: skip
                if feed_id == "reliefweb":
                    asked_as = (q.role,)  # one search a role: its search is the whole world (SRCH-35)
                if q.country in feed.countries and asked_as not in sent:
                    sent.add(asked_as)
                    mine.append(q)
            asks += [(feed_id, q) for q in mine[: feeds.config(feed_id).max_searches]]

    def ask(feed_id: str, query: feeds.FeedQuery) -> tuple[list[feeds.Lead], str]:
        guard = Guard(redis, feed_id)
        if feed_id == "reed":
            return reed.search(query, guard, graduate=bool(levels & {"graduate", "intern", "entry"}))
        if feed_id == "usajobs":
            return usajobs.search(query, guard, pathways=bool(levels & {"graduate", "intern"}))
        if feed_id == "adzuna":
            return adzuna.search(query, guard)
        if feed_id == "reliefweb":
            early = bool(levels) and levels <= {"intern", "graduate", "junior", "entry"}
            return reliefweb.search(
                query, guard, countries=countries, international=bool(profile and profile.international), early=early
            )
        return careerjet.search(query, guard, person=person)  # type: ignore[arg-type]

    with logfire.span("strategist feeds", asks=len(asks)) as span:
        with ThreadPoolExecutor(max_workers=max(1, min(8, len(asks)))) as pool:
            answers = list(pool.map(lambda a: (a, ask(*a)), asks))

        known = {canonical_url(u) or u for u in known_urls}
        taken: set[str] = set()
        tried: set[str] = set()  # a lead two APIs (or one API twice) returned is looked for once
        oldest = now - RECENCY_MS["21d"]
        found: list[Target] = []
        for (feed_id, query), (leads, how) in answers:
            stats = out.by_feed[feed_id]
            stats["searches"] += 1  # type: ignore[operator]
            stats["leads"] += len(leads)  # type: ignore[operator]
            stats["how"][how] += 1  # type: ignore[index]
            cfg, feed = feeds.config(feed_id), feeds.FEEDS[feed_id]
            for lead in leads:
                if stats["queued"] >= cfg.max_queued:  # type: ignore[operator]
                    break
                same = f"{lead.company.lower()}|{lead.title.lower()}"
                if same in tried or is_denied(lead.url) or (profile is not None and _excluded(lead.title, profile)):
                    continue
                if lead.date and _ms(lead.date) is not None and _ms(lead.date) < oldest:  # type: ignore[operator]
                    continue
                tried.add(same)
                if feed.full_text:
                    url, title, snippet, source, payload = lead.url, lead.title, lead.snippet, "feed", lead.payload
                else:
                    hit = finding.original(lead, serper=resource, sites=sites, gl=query.country, hl="en", exclude=exclude)  # type: ignore[arg-type]
                    if hit is None or (profile is not None and _excluded(hit.title, profile)):
                        stats["no_original"] = int(stats.get("no_original", 0)) + 1  # type: ignore[call-overload]
                        continue
                    url, title, snippet, source, payload = hit.url, hit.title, hit.snippet, "open", None
                if (canonical_url(url) or url) in known or (canonical_url(url) or url) in taken:
                    continue
                taken.add(canonical_url(url) or url)
                q = f"{feed.name}: {query.title}" + (f" in {query.city}" if query.city else "")
                found.append(
                    Target(
                        url,
                        host_of(url).removeprefix("www."),
                        source,
                        q,
                        len(found),
                        title,
                        snippet,
                        lead.date,
                        via=feed_id,
                        payload=payload,
                    )
                )
                stats["queued"] += 1  # type: ignore[operator]

        out.queued = queue.push(found)
        if resource is not None:
            out.errors += list(resource.errors)
        flat = {
            f"{feed_id}.{k}": (dict(v) if isinstance(v, Counter) else v)
            for feed_id, stats in out.by_feed.items()
            for k, v in stats.items()
        }
        span.set_attributes(flat | {"queued": out.queued, "resource_queries": resource.sent if resource else 0})
    return out


def _ms(day: str) -> int | None:
    from ..clock import parse_ms

    try:
        return parse_ms(day)
    except (TypeError, ValueError):
        return None


# SRCH-26 tier 4 is page 2 of a tier-1 query, and worth a credit only when page 1 was mostly
# postings: a query whose first ten results held two postings won't hold more on its second ten.
# Measured 13 Nov: 12 page-2 queries, sent for every tier-1 query, surfaced one job.
PAGE_TWO_MIN_POSTINGS = 5


def page_two_worth_asking(planned: list[PlannedQuery], postings: dict[str, int]) -> list[PlannedQuery]:
    """The plan with each page-2 query kept only if its page 1 returned enough postings. A page 1
    never sent (or not remembered) keeps its page 2 out: there is no evidence it is worth a credit."""
    return [q for q in planned if q.page == 1 or postings.get(q.q, 0) >= PAGE_TWO_MIN_POSTINGS]
