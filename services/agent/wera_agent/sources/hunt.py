"""The hunter (SRCH-22, 21 Nov): every page of jobs a search turns up, read for its postings.

A Google search for "Graduate Software Engineer, London" answers with postings, but as often with
pages of them: a board's results page, a category, an employer's careers page, an aggregator's copy
of one job. On 20 Nov 319 results held 41 postings and 13 such pages, and the old rule — 8 pages a
pass, 15 links each, plain HTTP, no next page — found nothing on them: 7 were drawn by JavaScript
(plain HTTP sees an empty shell), 5 refused WERA, 1 robots.txt disallowed. The person's diagnosis
was that results weren't being followed up, and it was right.

So each page now goes through, in order:

1. **Plain HTTP first.** robots.txt is asked, the page is fetched, and up to
   `hunt.links_per_listing` posting links are taken (`postings.posting_links`, SRCH-21's rules).
   Its "next page" link (`postings.next_page`) is followed up to `hunt.next_pages` times.
2. **Firecrawl's render**, a links-only scrape at 1 credit (`firecrawl_search.render_links`), only
   for a page that answered **200 with no posting links**: a JavaScript shell, not a refusal. At
   most `hunt.firecrawl_per_search` a search, and never past the run's credit cap.
3. **Never** a host that refused this search (a 401/403/429/503 or a bot check: the site saying
   no, which is not retried or worked around), a path robots.txt disallows, LinkedIn
   (`http.is_denied`), or the Adzuna and Careerjet tracked links (SRCH-32) — and an aggregator's
   pages are never rendered: a credit spent there buys more copies.

Each host's pages are read one after another, so a refusal stops that host before WERA asks it
again, and up to `hunt.workers` hosts at once (H4: the hunting step inside 40 s).

**The hunt cache.** The pages a search turned up are kept in a Redis set for the run,
`wera:hunt:<run>` (a day's TTL), and a page is hunted only the first time it is added: the same
results page comes back from several queries and several tiers, and is read once. Redis rather
than a set in memory so the record outlives the process that made it (a second worker, or a look
at what a search hunted); a hunter with no Redis keeps the set in memory, as the CLI and tests do.

**Aggregators' copies of one job** (bebee, jobleads, Indeed, `dedupe.AGGREGATORS`): the employer's
own posting is looked for with the same one Serper search the snippet APIs use
(`feeds.resource.original`), from the search's one lookup allowance (`lookups_per_search`, shared
with the job APIs' leads and the previews a short search reads), best match first and only at or
above the bar the Strategist gives. A copy looked for whose original isn't found is left as it was
before: a posting is still queued, anything else dropped. A copy *not* looked for — the allowance
spent, or below the bar — is a preview (22 Nov), read with its lookup when someone asks.

**Hunted links a search doesn't read become previews** (22 Nov). They carry no company, so what the
card says is only what is known (`employer_of`): the tenant of an employer's job system in the
link's address (Greenhouse, Lever, Ashby, Workday, SmartRecruiters…), the employer the link's
text names ("… at Acme"), or else nothing — the card says "Found on Totaljobs" (`site_name`).

Nothing here queues anything. The links found go back to `strategist.discover`, which sorts them
by the same rules as a search result (known, declined, excluded by title), ranks them by
`prefilter.lead_ranker` on their link text, and queues them as ordinary targets.
"""

import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import logfire

from ..domain import dedupe, places
from ..domain.dedupe import canonical_url
from ..errors import ApiError
from ..http import host_of, is_denied, request
from . import firecrawl_search, normalise
from .postings import links_from_urls, next_page, posting_links
from .robots import USER_AGENT, Robots, refusal

if TYPE_CHECKING:
    from ..config import HuntConfig, Site
    from .feeds import Lead
    from .firecrawl_search import SearchCaps, SearchOutcome
    from .serper import Result, Serper

KEY = "wera:hunt:"
KEY_TTL_SECONDS = 24 * 60 * 60
MAX_PAGE_BYTES = 3_000_000
# Pages recorded per page hunted in the span, so "postings found per page" can be read in Logfire.
SPAN_PAGES = 40

# Copies of one job that aren't in `dedupe.AGGREGATORS` because they are never a posting's link
# WERA keeps: Indeed disallows its posting paths, so its results are only worth the title.
COPY_HOSTS = (*dedupe.AGGREGATORS, "indeed.com", "indeed.co.uk")


class Refused(Exception):
    """A page the site wouldn't serve: a 403, a rate limit, a bot check. Never retried."""


def fetch_page(url: str) -> str | None:
    """The page over plain HTTP: its HTML on a 200, None otherwise (unreachable, a 404, a 500).
    Raises `Refused` when the site says no. A 200 is what earns a page a render, so nothing else
    may come back as HTML."""
    try:
        response = request("GET", url, follow_redirects=True, headers={"user-agent": USER_AGENT})
    except ApiError:
        raise
    except Exception:  # noqa: BLE001 — an unreachable page is one fewer page of links, nothing more
        return None
    html = response.content[:MAX_PAGE_BYTES].decode(response.encoding or "utf-8", errors="replace")
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    why = refusal(response.status_code, title.group(1) if title else "")
    if why:
        raise Refused(why)
    # A redirect is followed by httpx, past `http.check`: one that lands on LinkedIn read nothing.
    if is_denied(str(response.url)):
        return None
    return html if response.status_code == 200 else None


def _bare(url: str) -> str:
    return host_of(url).removeprefix("www.")


def _on(host: str, suffixes: Iterable[str]) -> bool:
    return any(host == s or host.endswith(f".{s}") for s in suffixes)


def never(url: str) -> bool:
    """A page the hunter never requests: LinkedIn (SEC-07) and the tracked API links (SRCH-32)."""
    return is_denied(url) or normalise.is_tracked_link(url)


def is_copy(url: str, sites: "list[Site] | None" = None) -> bool:
    """An aggregator's page: someone else's posting again. A board in `config/sites.yaml` is not
    one, whatever `dedupe.AGGREGATORS` says — eFinancialCareers is both, and for finance it is a
    main board (20 Nov)."""
    host = _bare(url)
    if not _on(host, COPY_HOSTS):
        return False
    boards = {s.domain.lower().removeprefix("www.") for s in sites or []}
    return not _on(host, boards)


@dataclass(frozen=True)
class Found:
    """A posting link a page gave: where, its link text, and the page it was on."""

    url: str
    text: str
    page: str
    rendered: bool = False


@dataclass
class Hunted:
    """What hunting did — one pass's, or a whole search's when summed (`add`)."""

    listings: int = 0  # pages hunted: the listings themselves, read or rendered
    pages: int = 0  # requests over plain HTTP, next pages included
    followed: int = 0  # next pages read
    rendered: int = 0  # listings Firecrawl drew
    credits: int = 0
    refused: int = 0  # pages that refused WERA
    skipped: int = 0  # pages not asked because their host refused earlier in the search
    disallowed: int = 0  # pages robots.txt disallows (or whose robots.txt couldn't be read)
    never: int = 0  # LinkedIn, a tracked link
    over_cap: int = 0  # pages past `listings_per_search`
    render_capped: int = 0  # JavaScript shells left unrendered: the search's or the run's cap
    empty: int = 0  # pages hunted that gave no posting links at all
    found: int = 0  # posting links found
    new: int = 0  # of those, queued (set by the Strategist, which does the queueing)
    copies: int = 0  # aggregators' copies of one job
    lookups: int = 0  # Serper searches for their originals
    originals: int = 0  # originals found
    by_page: dict[str, int] = field(default_factory=dict)

    def add(self, other: "Hunted") -> None:
        for name in self.__dataclass_fields__:
            if name == "by_page":
                for url, n in other.by_page.items():
                    if len(self.by_page) < SPAN_PAGES:
                        self.by_page[url] = n
            else:
                setattr(self, name, getattr(self, name) + getattr(other, name))


class Hunter:
    """One search's hunter: its caps, its refusals and its hunt cache last across the search's
    passes (each tier is a `strategist.discover` call), so 40 pages is 40 a search, not a tier.

    `fetch` reads a page over plain HTTP (`fetch_page`; tests pass their own). Renders happen only
    with `spend` and `caps` (the run's Firecrawl accounting) and `render` true. `lookup` is the
    Serper client for aggregators' originals, with its own small cap — or a factory for one.
    """

    def __init__(
        self,
        *,
        config: "HuntConfig | None" = None,
        robots: Robots | None = None,
        fetch: Callable[[str], str | None] | None = None,
        redis: Any | None = None,
        run: str | None = None,
        spend: "SearchOutcome | None" = None,
        caps: "SearchCaps | None" = None,
        render: bool = False,
        lookup: "Serper | Callable[[], Serper] | None" = None,
        countries: "list[str] | None" = None,
    ) -> None:
        # SRCH-32 (22 Nov): the ticked countries, whose floors the copies' lookups fill first.
        self.countries = [c.lower() for c in countries or []]
        if config is None:
            from ..config import wera

            config = wera().hunt
        self.cfg = config
        self.robots = robots or Robots()
        self.fetch = fetch or fetch_page
        self.redis = redis
        self.key = f"{KEY}{run}" if run else None
        self.spend, self.caps = spend, caps
        self.render = render and spend is not None and caps is not None
        # Made on first use when given as a factory: most searches have no copy to trace, and a
        # client that is never asked shouldn't exist (nor be counted).
        self._make_lookup = lookup if callable(lookup) and not hasattr(lookup, "search") else None
        self.lookup: Serper | None = None if self._make_lookup else lookup  # type: ignore[assignment]
        self.refused: set[str] = set()
        self.total = Hunted()
        self._seen: set[str] = set()
        self._lock = threading.Lock()
        self._listings = 0
        self._renders = 0

    # -- the hunt cache ----------------------------------------------------------------------

    def cache(self, url: str) -> bool:
        """Add a page to this search's hunt cache. True the first time: a page to hunt."""
        key = canonical_url(url) or url
        if self.redis is not None and self.key is not None:
            try:
                added = bool(self.redis.sadd(self.key, key))
                self.redis.expire(self.key, KEY_TTL_SECONDS)
                return added
            except Exception:  # noqa: BLE001 — a Redis hiccup costs the record, never the hunt
                logfire.warn("Couldn't record a page in the hunt cache; kept in memory instead")
        if key in self._seen:
            return False
        self._seen.add(key)
        return True

    # -- listings ----------------------------------------------------------------------------

    def hunt(self, urls: list[str], *, on_host: Callable[[int, int], None] | None = None) -> tuple[list[Found], Hunted]:
        """Hunt these pages, `cfg.workers` hosts at a time, each host's pages in order. Returns the
        posting links found, in the pages' order, and what this call did. `on_host(done, total)`
        is told on the calling thread as each host finishes, for the run's progress bar."""
        by_host: dict[str, list[str]] = {}
        for url in urls:
            by_host.setdefault(_bare(url), []).append(url)
        out = Hunted()
        results: dict[str, list[Found]] = {}
        if by_host:
            workers = max(1, min(self.cfg.workers, len(by_host)))
            with logfire.span("hunt", pages=len(urls), hosts=len(by_host)) as span:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(self._host, pages, out): host for host, pages in by_host.items()}
                    for done, future in enumerate(as_completed(futures), 1):
                        results[futures[future]] = future.result()
                        if on_host is not None:
                            on_host(done, len(futures))
                span.set_attributes(_attributes(out))
        found = [f for host in by_host for f in results.get(host, [])]
        with self._lock:
            self.total.add(out)
        return found, out

    def _host(self, pages: list[str], out: Hunted) -> list[Found]:
        found: list[Found] = []
        for url in pages:
            try:
                found += self._listing(url, out)
            except ApiError:
                continue  # `http.check` said no to the address: nothing was asked
            except Exception:  # noqa: BLE001 — one page's surprise is never the search's
                logfire.exception("Couldn't hunt {url}", url=url)
        return found

    def _allowed(self, url: str, out: Hunted) -> bool:
        try:
            allowed = self.robots.allows(url)
        except ApiError:
            allowed = False
        if not allowed:
            with self._lock:
                out.disallowed += 1
            logfire.info("{url} not read: robots.txt disallows it", url=url)
        return allowed

    def _listing(self, url: str, out: Hunted) -> list[Found]:
        host = _bare(url)
        with self._lock:
            if never(url):
                out.never += 1
                return []
            if host in self.refused:
                out.skipped += 1
                return []
        if not self._allowed(url, out):
            return []
        with self._lock:
            if self._listings >= self.cfg.listings_per_search:
                out.over_cap += 1
                return []
            self._listings += 1
            out.listings += 1
            out.pages += 1
        try:
            html = self.fetch(url)
        except Refused as why:
            self._refuse(host, url, str(why), out)
            return []
        limit = self.cfg.links_per_listing
        links = posting_links(html, url, host_of(url), limit) if html else []
        rendered = False
        if html is not None and not links:
            links, rendered = self._render(url, host, limit, out), True
        if not rendered:
            links += self._follow(url, html, host, links, out)
        kept = [Found(u, text, url, rendered) for u, text in links[:limit]]
        with self._lock:
            out.found += len(kept)
            out.empty += not kept
            if len(out.by_page) < SPAN_PAGES:
                out.by_page[url] = len(kept)
        if not kept:
            # A shell left unrendered, or a page whose links aren't postings (an advice page, a
            # company directory): either way nothing to queue, and Logfire says which page.
            logfire.info("{url} gave no posting links", url=url)
        return kept

    def _refuse(self, host: str, url: str, why: str, out: Hunted) -> None:
        with self._lock:
            self.refused.add(host)
            out.refused += 1
        logfire.info("{url} refused ({why}); not asking {host} again this search", url=url, why=why, host=host)

    def _follow(self, url: str, html: str | None, host: str, links: list[tuple[str, str]], out: Hunted) -> list[tuple[str, str]]:
        """The listing's next pages, up to `cfg.next_pages`, while each gives new posting links."""
        more: list[tuple[str, str]] = []
        seen = {canonical_url(u) or u for u, _t in links}
        page, current = html, url
        for _ in range(self.cfg.next_pages):
            if not page or not links or len(links) + len(more) >= self.cfg.links_per_listing:
                break
            nxt = next_page(page, current)
            if nxt is None or not self._allowed(nxt, out):
                break
            with self._lock:
                if host in self.refused:
                    break
                out.pages += 1
                out.followed += 1
            try:
                page = self.fetch(nxt)
            except Refused as why:
                self._refuse(host, nxt, str(why), out)
                break
            fresh = [(u, t) for u, t in (posting_links(page, nxt, host_of(nxt), self.cfg.links_per_listing) if page else [])
                     if (canonical_url(u) or u) not in seen]  # fmt: skip
            if not fresh:
                break
            seen |= {canonical_url(u) or u for u, _t in fresh}
            more += fresh
            current = nxt
        return more

    def _render(self, url: str, host: str, limit: int, out: Hunted) -> list[tuple[str, str]]:
        """A JavaScript shell drawn by Firecrawl, inside the search's and the run's caps."""
        if not self.render or _on(host, COPY_HOSTS):
            if self.render:
                logfire.info("{url} is an aggregator's page: not rendered", url=url)
            return []
        with self._lock:
            if self._renders >= self.cfg.firecrawl_per_search:
                out.render_capped += 1
                return []
            self._renders += 1
        page = firecrawl_search.render_links(url, self.spend, self.caps, lock=self._lock)  # type: ignore[arg-type]
        with self._lock:
            if page is None:
                # The run's credit cap left no room: nothing charged, and counted as capped.
                self._renders -= 1
                out.render_capped += 1
                return []
            out.rendered += 1
            out.credits += firecrawl_search.LINKS_SCRAPE_CREDITS
        if page.failed:
            return []
        links = posting_links(page.html, url, host_of(url), limit) if page.html else []
        if not links and page.links:
            links = links_from_urls(page.links, url, host_of(url), limit)
        logfire.info("Rendered {url} with Firecrawl: {n} posting links", url=url, n=len(links))
        return links

    # -- aggregators' copies -----------------------------------------------------------------

    def originals(
        self,
        copies: list[tuple["Result", str, str, list[str]]],
        *,
        sites: "list[Site]",
        scores: "list[float | None] | None" = None,
        bar: float | None = None,
    ) -> tuple[list[tuple[str, "Result | None"]], Hunted]:
        """What became of each aggregator's copy — (result, gl, hl, hosts to leave out) — in the
        order given, as (what, original):

        * `found`: its original, found with one Serper search from the search's allowance;
        * `none`: looked for, and nothing found;
        * `untraceable`: no title and company to search for, so never looked for;
        * `waiting`: not looked for — the allowance spent, or its match (`scores`, the queue's
          ranker on its title and summary) under `bar` — and so a preview, read when asked.

        The lookups go best match first, whatever order the copies came in (22 Nov) — each ticked
        country's floor of what is left of the allowance first (`targets.floors_first`), then the
        rest best match first."""
        from .feeds import resource
        from .targets import floors_first

        out = Hunted(copies=len(copies))
        what: list[tuple[str, Result | None]] = [("untraceable", None)] * len(copies)
        leads = [copy_lead(result) for result, _gl, _hl, _x in copies]
        if any(leads) and self.lookup is None and self._make_lookup is not None:
            self.lookup = self._make_lookup()
        ranked = scores if scores is not None and len(scores) == len(copies) else [None] * len(copies)
        order = sorted(range(len(copies)), key=lambda i: -(ranked[i] if ranked[i] is not None else 0.0))
        if self.lookup is not None and self.countries:
            # The floors first: the copies that may take a lookup, ranked, shared out by country;
            # those go to the front, the rest follow best match first.
            eligible = [i for i in order if leads[i] is not None and not (bar is not None and ranked[i] is not None and ranked[i] < bar)]  # type: ignore[operator]  # fmt: skip
            room = max(self.lookup.max_queries - self.lookup.sent, 0)
            first = [eligible[j] for j in floors_first(eligible, room, self.countries, lambda i: copies[i][1])]
            ahead = set(first)
            order = [*first, *(i for i in order if i not in ahead)]
        for i in order:
            lead = leads[i]
            if lead is None:
                continue
            under = bar is not None and ranked[i] is not None and ranked[i] < bar  # type: ignore[operator]
            if under or self.lookup is None or self.lookup.stopped or self.lookup.sent >= self.lookup.max_queries:
                what[i] = ("waiting", None)
                continue
            result, gl, hl, exclude = copies[i]
            before = self.lookup.sent
            hit = resource.original(lead, serper=self.lookup, sites=sites, gl=gl, hl=hl, exclude=exclude)
            out.lookups += self.lookup.sent - before
            out.originals += hit is not None
            what[i] = ("found", hit) if hit is not None else ("none", None)
        with self._lock:
            self.total.add(out)
        return what, out


# -- reading an aggregator's title --------------------------------------------------------------

# How aggregators write a job's title in a search result: "Graduate Analyst - Acme - London |
# beBee", "Graduate Analyst job in London at Acme | JobLeads", "Graduate Analyst - Acme Ltd -
# London - Indeed.com". The site's own name and the place are dropped; the company is what's left.
_SEPARATORS = re.compile(r"\s+[|–—-]\s+|\s*\|\s*")
_AT = re.compile(r"^(?P<title>.+?)(?:\s+jobs?)?(?:\s+in\s+[^|]+?)?\s+(?:at|@)\s+(?P<company>[^|]+?)\s*(?:\||$)", re.I)
_A_LIST = re.compile(r"\b\d[\d,.]*\+?\s+jobs?\b|\bjobs\s+in\b|\bjobs?\s*$|\bvacancies\b", re.I)
_SITE_WORDS = re.compile(
    r"\b(?:indeed|bebee|jobleads|jooble|talent|simplyhired|jobrapido|neuvoo|whatjobs|adzuna|careerjet)\b|\.(?:com|co\.uk|org|ai)\b",
    re.I,
)
_NOT_A_COMPANY = {"remote", "hybrid", "full time", "full-time", "part time", "permanent", "contract", "graduate", "uk", "united kingdom"}  # fmt: skip


def copy_lead(result: "Result") -> "Lead | None":
    """An aggregator's result as a lead to look for: its title and the company it names. None
    when the result is a page of jobs ("1,204 Graduate Analyst jobs in London") or names no
    company — a search for a title alone finds the title anywhere, which is not this job."""
    from .feeds import Lead

    text = " ".join((result.title or "").split())
    if not text or _A_LIST.search(text):
        return None
    title = company = ""
    if m := _AT.match(text):
        title, company = m["title"], m["company"]
    else:
        parts = [p.strip(" ,") for p in _SEPARATORS.split(text) if p.strip(" ,")]
        parts = [p for p in parts if not _SITE_WORDS.search(p)]
        if len(parts) >= 2:
            title = parts[0]
            company = next((p for p in parts[1:] if not _is_place(p) and p.casefold() not in _NOT_A_COMPANY), "")
    company = _SITE_WORDS.sub("", company).strip(" -|,")
    if not title or not company or _is_place(company):
        return None
    return Lead(url=result.url, title=title, company=company, location="", date=None, snippet=result.snippet or "")


# -- what a hunted link says about itself (22 Nov) -----------------------------------------------

# How sites write their own names, where the address alone would read wrong ("Bebee", "Jobleads").
SITE_NAMES = dedupe.SITE_NAMES
# The labels a host starts with that are never the site's name ("uk.bebee.com", "jobs.example.com").
_HOST_FILLER = {"www", "uk", "en", "m", "jobs", "careers", "apply", "boards", "job-boards", "job"}
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov"}


def site_name(url: str, sites: "list[Site] | None" = None) -> str:
    """A site's name, as a card shows it: its name in `config/sites.yaml`, else how it writes it
    (`SITE_NAMES`), else its address's own name, capitalised ("reed.co.uk" → "Reed")."""
    host = _bare(url)
    for site in sites or []:
        # A name written as an address ("www.milkround.com", as discovery saves some) reads as one.
        if _on(host, [site.domain.lower().removeprefix("www.")]) and not re.fullmatch(r"[\w.-]+\.[a-z]{2,}", site.name):
            return site.name
    for domain, name in SITE_NAMES.items():
        if _on(host, [domain]):
            return name
    labels = [p for p in host.split(".") if p]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        labels = labels[:-2]
    elif len(labels) >= 2:
        labels = labels[:-1]
    name = next((p for p in reversed(labels) if p not in _HOST_FILLER), labels[-1] if labels else host)
    return name[:1].upper() + name[1:]


# Employer job systems whose addresses name the employer, and where: the first label of the host
# (`acme.wd3.myworkdayjobs.com`, `acme.teamtailor.com`) or the first part of the path
# (`jobs.lever.co/acme/…`, `jobs.smartrecruiters.com/Acme/…`).
_TENANT_IN_HOST = ("myworkdayjobs.com", "teamtailor.com", "recruitee.com", "personio.de", "personio.com", "bamboohr.com", "workable.com", "breezy.hr")  # fmt: skip
_TENANT_IN_PATH = ("smartrecruiters.com", "workable.com", "greenhouse.io", "lever.co", "ashbyhq.com")
_NOT_A_TENANT = {"www", "jobs", "careers", "apply", "boards", "job-boards", "embed", "api", "en", "uk", "us", "o", "j", "job"}


def ats_employer(url: str) -> str | None:
    """The employer an employer's job system's address names, display-cased ("acme-robotics" →
    "Acme Robotics"), or None: a board's address names no employer, and none is guessed."""
    from urllib.parse import urlparse

    from .resolve import ats_from_url

    host = _bare(url)
    token: str | None = None
    if (ats := ats_from_url(url)) is not None:
        token = ats.board_token
    elif _on(host, _TENANT_IN_HOST) and host.count(".") >= 2 and host.split(".")[0] not in _NOT_A_TENANT:
        token = host.split(".")[0]
    elif _on(host, _TENANT_IN_PATH):
        parts = [p for p in urlparse(url).path.split("/") if p]
        token = parts[0] if parts and parts[0].lower() not in _NOT_A_TENANT else None
    if not token:
        return None
    token = re.sub(r"\.(?:com|co\.uk|io|ai|co|org|net|de|ch|uk)$", "", token, flags=re.I)  # Ashby's "partly.com"
    words = [w for w in re.split(r"[-_.\s]+", token) if w]
    if not words or all(w.isdigit() for w in words):
        return None
    return " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in words)


def employer_of(url: str, text: str) -> tuple[str, str | None]:
    """A hunted link's title and employer, from what it says about itself: the employer its
    address names (`ats_employer`), else the one its text names ("Graduate Analyst at Acme"), else
    None. The title is the link's text, less the employer it named."""
    title = " ".join((text or "").split())
    if (m := _AT.match(title)) is not None and not _is_place(m["company"]) and not _SITE_WORDS.search(m["company"]):
        title, named = m["title"].strip(), m["company"].strip(" -|,")
    else:
        named = None
    return title, ats_employer(url) or named or None


def _is_place(text: str) -> bool:
    """A place, as a whole: "London", "London, England, United Kingdom" — not a company that
    happens to start with a town's name ("Reading Room Ltd")."""
    first = places.fold(text.split(",")[0])
    return bool(first) and (first in places.ALIAS_INDEX or bool(places.places_named(first)))


def _attributes(out: Hunted) -> dict[str, Any]:
    """A hunt's figures for its Logfire span: one attribute each, the pages as their own dict."""
    flat = {k: v for k, v in vars(out).items() if k != "by_page"}
    return flat | {"postings_by_page": dict(out.by_page), "postings_per_page": dict(Counter(out.by_page.values()))}
