"""SRCH-22 (21 Nov): the hunter reads every page of jobs a search turns up for its postings.

Plain HTTP first, robots.txt asked; the page's next pages followed, twice at most; Firecrawl's
links-only render for a page that answered 200 with no posting links — a JavaScript shell — inside
the search's and the run's credit caps. A host that refused is never asked again or rendered, a
path robots.txt disallows is never read, and an aggregator's copy of one job is traced to the
employer's own posting. On 20 Nov, 13 results pages read the old way found 0 postings.
"""

import threading
import time
from types import SimpleNamespace

import fakeredis
import httpx
import pytest
import respx

from wera_agent.config import HuntConfig, Site
from wera_agent.nodes import strategist
from wera_agent.schemas.strategy import PlannedQuery
from wera_agent.sources import firecrawl_search, hunt, postings
from wera_agent.sources.firecrawl_search import SearchCaps, SearchOutcome
from wera_agent.sources.robots import Robots
from wera_agent.sources.serper import Result
from wera_agent.sources.targets import TargetQueue

BOARDS = [
    Site(domain="builtinlondon.uk", name="Built In London", terms_reviewed_on="2026-01-01", countries=["GB"]),
    Site(domain="www.efinancialcareers.com", name="eFinancialCareers", terms_reviewed_on="2026-01-01", countries=["GB"]),
]
QUERY = PlannedQuery(q='"Graduate Software Engineer" job in London -site:linkedin.com', gl="gb", hl="en", source="open", tier=1, title="Graduate Software Engineer")  # fmt: skip

# A board's results page drawn by JavaScript: plain HTTP gets a shell with no links in it.
SHELL = '<html><head><title>Jobs</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
DRAWN = """
<a href="/job/graduate-software-engineer/12000001">Graduate Software Engineer</a>
<a href="/job/graduate-data-engineer/12000002">Graduate Data Engineer</a>
<a href="/jobs/remote">Remote jobs</a>
"""


class FakeSerper:
    def __init__(self, answers: dict[str, list[Result]], max_queries: int = 30):
        self.answers, self.max_queries = answers, max_queries
        self.sent = self.answered = self.cached = self.coalesced = 0
        self.errors: list[str] = []
        self.stopped: str | None = None
        self.asked: list[str] = []

    @property
    def credits_used(self) -> int:
        return self.answered

    def search(self, q: str, *, gl: str, hl: str, page: int = 1) -> list[Result]:
        self.asked.append(q)
        self.sent += 1
        self.answered += 1
        return next((rs for key, rs in self.answers.items() if key in q), [])


class AllowAll:
    def allows(self, _url: str) -> bool:
        return True


class FakeFirecrawl:
    """Firecrawl's client, drawing every page it is asked for as `DRAWN`."""

    def __init__(self, html: str = DRAWN, links: list[str] | None = None):
        self.html, self.links, self.asked = html, links or [], []

    def scrape(self, url, *, formats, only_main_content):
        self.asked.append((url, tuple(formats)))
        return SimpleNamespace(html=self.html, links=self.links, metadata=None)


@pytest.fixture
def firecrawl(monkeypatch) -> FakeFirecrawl:
    fake = FakeFirecrawl()
    monkeypatch.setattr(firecrawl_search, "_client", lambda: fake)
    return fake


@pytest.fixture
def queue() -> TargetQueue:
    return TargetQueue(fakeredis.FakeRedis(decode_responses=True), "5-abc")


def result(url: str, title: str = "Graduate jobs", snippet: str = "", n: int = 1) -> Result:
    return Result(url=url, title=title, snippet=snippet, date=None, position=n)


def hunter(fetch, *, credits: int = 100, render: bool = True, **config) -> hunt.Hunter:
    return hunt.Hunter(
        config=HuntConfig(**config),
        robots=AllowAll(),  # type: ignore[arg-type]
        fetch=fetch,
        spend=SearchOutcome(),
        caps=SearchCaps(max_credits=credits),
        render=render,
    )


def discover(serper: FakeSerper, queue: TargetQueue, who: hunt.Hunter, *, known: set[str] | None = None) -> strategist.Discovery:
    return strategist.discover([QUERY], [1], serper=serper, queue=queue, known_urls=known or set(), sites=BOARDS, hunter=who)  # type: ignore[arg-type]


class TestAJavaScriptShell:
    def test_is_rendered_by_firecrawl_and_its_links_queued(self, queue, firecrawl):
        listing = "https://builtinlondon.uk/jobs?search=graduate"
        who = hunter(lambda _url: SHELL)
        out = discover(FakeSerper({"Graduate": [result(listing)]}), queue, who)
        assert firecrawl.asked == [(listing, ("links", "html"))]
        assert [t.url for t in queue.peek()] == [
            "https://builtinlondon.uk/job/graduate-software-engineer/12000001",
            "https://builtinlondon.uk/job/graduate-data-engineer/12000002",
        ]
        assert [t.title for t in queue.peek()] == ["Graduate Software Engineer", "Graduate Data Engineer"]
        assert (out.hunted.rendered, out.hunted.credits, who.spend.credits_used) == (1, 1, 1)
        assert (out.hunted.found, out.hunted.new) == (2, 2)

    def test_a_links_only_answer_is_read_by_its_urls(self, queue, monkeypatch):
        fake = FakeFirecrawl(html="", links=["https://builtinlondon.uk/job/graduate-analyst/12000009", "https://elsewhere.example/job/x/12345678"])  # fmt: skip
        monkeypatch.setattr(firecrawl_search, "_client", lambda: fake)
        discover(FakeSerper({"Graduate": [result("https://builtinlondon.uk/jobs?search=x")]}), queue, hunter(lambda _u: SHELL))
        assert [(t.url, t.title) for t in queue.peek()] == [
            ("https://builtinlondon.uk/job/graduate-analyst/12000009", "Graduate Analyst")
        ]

    def test_a_page_that_didnt_answer_200_is_not_rendered(self, queue, firecrawl):
        """None from plain HTTP is a 404, a 500 or no answer: not a shell, and not worth a credit."""
        out = discover(
            FakeSerper({"Graduate": [result("https://builtinlondon.uk/jobs?search=x")]}), queue, hunter(lambda _u: None)
        )
        assert firecrawl.asked == [] and out.hunted.rendered == 0

    def test_an_aggregators_page_is_never_rendered(self, queue, firecrawl):
        out = discover(
            FakeSerper({"Graduate": [result("https://uk.bebee.com/jobs/graduate-software-engineer")]}),
            queue,
            hunter(lambda _u: SHELL),
        )
        assert firecrawl.asked == [] and out.hunted.listings == 1


class TestARefusal:
    def test_a_refused_host_is_never_rendered_or_asked_again(self, queue, firecrawl):
        asked: list[str] = []

        def refuse(url: str) -> str:
            asked.append(url)
            raise strategist.Refused("HTTP 403")

        pages = [result(f"https://www.gradcracker.com/search/technology-it/graduate-jobs?page={n}", n=n) for n in range(2, 6)]
        out = discover(FakeSerper({"Graduate": pages}), queue, hunter(refuse))
        assert len(asked) == 1 and firecrawl.asked == []
        assert out.refused == {"gradcracker.com"} and (out.hunted.refused, out.hunted.skipped) == (1, 3)

    def test_a_refusal_lasts_the_whole_search(self, queue, firecrawl):
        """The hunter is the search's, so a host that refused in tier 1 isn't asked in tier 2."""
        asked: list[str] = []

        def refuse(url: str) -> str:
            asked.append(url)
            raise strategist.Refused("a bot-check page")

        who = hunter(refuse)
        discover(FakeSerper({"Graduate": [result("https://www.gradcracker.com/search/a?page=2")]}), queue, who)
        again = discover(FakeSerper({"Graduate": [result("https://www.gradcracker.com/search/b?page=3")]}), queue, who)
        assert len(asked) == 1 and again.hunted.skipped == 1 and firecrawl.asked == []

    def test_linkedin_and_the_tracked_api_links_are_never_requested(self, firecrawl):
        fetched: list[str] = []
        who = hunter(lambda url: fetched.append(url) or SHELL)
        _links, did = who.hunt(["https://www.linkedin.com/jobs/search?keywords=x", "https://www.adzuna.co.uk/jobs/search?q=x", "https://www.jobviewtrack.com/en-gb/job-1/"])  # fmt: skip
        assert fetched == [] and firecrawl.asked == [] and did.never == 3


class TestRobotsTxt:
    @respx.mock
    def test_a_disallowed_path_is_never_read_and_an_allowed_one_is(self, outbound, queue, firecrawl):
        respx.get("https://jobs.example.org/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /search\n")
        )
        barred = respx.get("https://jobs.example.org/search?q=graduate").mock(return_value=httpx.Response(200, text=DRAWN))
        allowed = respx.get("https://jobs.example.org/jobs/graduate").mock(
            return_value=httpx.Response(200, text=DRAWN.replace("/job/", "/jobs/"))
        )
        who = hunt.Hunter(config=HuntConfig(), robots=Robots(), spend=SearchOutcome(), caps=SearchCaps(), render=True)
        links, did = who.hunt(["https://jobs.example.org/search?q=graduate", "https://jobs.example.org/jobs/graduate"])
        assert not barred.called and allowed.called and firecrawl.asked == []
        assert did.disallowed == 1 and did.listings == 1
        assert [f.url for f in links] == ["https://jobs.example.org/jobs/graduate-software-engineer/12000001", "https://jobs.example.org/jobs/graduate-data-engineer/12000002"]  # fmt: skip

    @respx.mock
    def test_a_next_page_robots_disallows_is_not_followed(self, outbound):
        respx.get("https://jobs.example.org/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /*page=\n")
        )
        first = '<link rel="next" href="/jobs/graduate?page=2"><a href="/jobs/graduate-analyst/12000001">Graduate Analyst</a>'
        respx.get("https://jobs.example.org/jobs/graduate").mock(return_value=httpx.Response(200, text=first))
        second = respx.get("https://jobs.example.org/jobs/graduate?page=2").mock(return_value=httpx.Response(200, text=DRAWN))
        who = hunt.Hunter(config=HuntConfig(), robots=Robots())
        links, did = who.hunt(["https://jobs.example.org/jobs/graduate"])
        assert not second.called and len(links) == 1 and (did.followed, did.disallowed) == (0, 1)


class TestPagination:
    @staticmethod
    def pages(fetched: list[str]):
        def fetch(url: str) -> str:
            fetched.append(url)
            n = int(url.rsplit("page=", 1)[1]) if "page=" in url else 1
            return f'<a rel="next" href="/jobs?search=x&page={n + 1}">Next</a><a href="/job/role-{n}/{12000000 + n}">Graduate role {n}</a>'

        return fetch

    def test_next_pages_are_followed_twice_at_most(self, queue):
        fetched: list[str] = []
        out = discover(
            FakeSerper({"Graduate": [result("https://builtinlondon.uk/jobs?search=x")]}), queue, hunter(self.pages(fetched))
        )
        assert fetched == ["https://builtinlondon.uk/jobs?search=x", "https://builtinlondon.uk/jobs?search=x&page=2", "https://builtinlondon.uk/jobs?search=x&page=3"]  # fmt: skip
        assert (out.hunted.followed, out.hunted.pages, out.hunted.found) == (2, 3, 3) and queue.size() == 3

    def test_a_next_page_with_nothing_new_ends_the_walk(self):
        fetched: list[str] = []

        def fetch(url: str) -> str:
            fetched.append(url)
            return '<a rel="next" href="/jobs?page=9">Next</a><a href="/job/role/12000001">Graduate role</a>'

        hunter(fetch).hunt(["https://builtinlondon.uk/jobs?page=1"])
        assert len(fetched) == 2  # page 9 repeated page 1's posting, so there is no page 10

    def test_how_a_page_names_its_next(self):
        here = "https://board.example/jobs?q=analyst&page=2"
        assert (
            postings.next_page('<link rel="next" href="?q=analyst&page=3">', here)
            == "https://board.example/jobs?q=analyst&page=3"
        )
        assert postings.next_page('<a href="/jobs?q=analyst&page=3" aria-label="Next page">›</a>', here) == "https://board.example/jobs?q=analyst&page=3"  # fmt: skip
        assert postings.next_page('<a href="/jobs?q=analyst&page=3">3</a><a href="/jobs?q=analyst&page=1">1</a>', here) == "https://board.example/jobs?q=analyst&page=3"  # fmt: skip
        assert postings.next_page('<a href="/jobs?q=other&page=3">3</a>', here) is None  # a different search
        assert postings.next_page('<a rel="next" href="https://elsewhere.example/jobs?page=3">Next</a>', here) is None
        assert postings.next_page('<a href="/jobs/remote">More jobs</a>', here) is None


class TestTheCaps:
    def test_the_runs_credit_cap_stops_rendering(self, queue, firecrawl):
        shells = [result(f"https://board{i}.example/jobs?search=x", n=i) for i in range(4)]
        who = hunter(lambda _u: SHELL, credits=2)
        out = discover(FakeSerper({"Graduate": shells}), queue, who)
        assert len(firecrawl.asked) == 2 and who.spend.credits_used == 2
        assert (out.hunted.rendered, out.hunted.render_capped) == (2, 2)
        assert who.spend.capped is None  # a render is a way to find more: the run isn't partial for it

    def test_the_searchs_render_cap_stops_rendering(self, queue, firecrawl):
        shells = [result(f"https://board{i}.example/jobs?search=x", n=i) for i in range(4)]
        out = discover(FakeSerper({"Graduate": shells}), queue, hunter(lambda _u: SHELL, firecrawl_per_search=1))
        assert len(firecrawl.asked) == 1 and (out.hunted.rendered, out.hunted.render_capped) == (1, 3)

    def test_the_listings_cap_is_the_searchs(self, queue):
        fetched: list[str] = []
        who = hunter(lambda url: fetched.append(url) or DRAWN, render=False, listings_per_search=3)
        discover(FakeSerper({"Graduate": [result(f"https://board{i}.example/jobs?search=a", n=i) for i in range(2)]}), queue, who)
        again = discover(
            FakeSerper({"Graduate": [result(f"https://board{i}.example/jobs?search=b", n=i) for i in range(2)]}), queue, who
        )
        assert len(fetched) == 3 and again.hunted.over_cap == 1

    def test_pages_are_read_four_hosts_at_a_time_and_each_host_in_turn(self):
        lock = threading.Lock()
        now: dict[str, int] = {}
        most = {"all": 0, "host": 0}

        def fetch(url: str) -> str:
            host = hunt._bare(url)
            with lock:
                now[host] = now.get(host, 0) + 1
                most["all"] = max(most["all"], sum(now.values()))
                most["host"] = max(most["host"], now[host])
            time.sleep(0.03)
            with lock:
                now[host] -= 1
            return DRAWN

        urls = [f"https://board{i % 6}.example/jobs?search={i}" for i in range(12)]
        hunter(fetch, render=False).hunt(urls)
        assert 1 < most["all"] <= 4 and most["host"] == 1


class TestWhatIsQueued:
    def test_links_are_classified_and_deduped_against_known_and_the_queue(self, queue):
        page = """
        <a href="/job/graduate-swe/12000001">Graduate Software Engineer</a>
        <a href="/job/graduate-swe/12000001?utm_source=x">Graduate Software Engineer (again)</a>
        <a href="/job/graduate-qa/12000002">Graduate QA Engineer</a>
        <a href="/job/graduate-sre/12000003">Graduate SRE</a>
        <a href="/jobs/remote">Remote jobs</a>
        <a href="/blog/how-to-apply">How to apply</a>
        <a href="https://elsewhere.example/job/x/99999999">An advert</a>
        """
        from wera_agent.sources.targets import Target

        queue.push([Target("https://builtinlondon.uk/job/graduate-sre/12000003", "builtinlondon.uk", "open", "q", 0, "Graduate SRE", "", None)])  # fmt: skip
        out = discover(
            FakeSerper({"Graduate": [result("https://builtinlondon.uk/jobs?search=x")]}),
            queue,
            hunter(lambda _u: page, render=False),
            known={"https://www.builtinlondon.uk/job/graduate-qa/12000002"},
        )
        assert (out.hunted.found, out.known, out.hunted.new) == (3, 1, 1)
        assert [t.url for t in queue.peek()][-1] == "https://builtinlondon.uk/job/graduate-swe/12000001"
        assert queue.size() == 2

    def test_the_queues_ranker_orders_them_by_their_link_text(self):
        seen: list[list[tuple[str, str]]] = []

        def rank(items):
            seen.append(items)
            return [1.0 if "Analyst" in title else 0.0 for title, _ in items]

        queue = TargetQueue(fakeredis.FakeRedis(decode_responses=True), "5-abc", rank=rank)
        page = '<a href="/job/barista/12000001">Barista</a><a href="/job/graduate-analyst/12000002">Graduate Analyst</a>'
        discover(
            FakeSerper({"Graduate": [result("https://builtinlondon.uk/jobs?search=x")]}),
            queue,
            hunter(lambda _u: page, render=False),
        )
        assert seen[0] == [("Barista", ""), ("Graduate Analyst", "")]
        assert [t.rank for t in sorted(queue.peek(), key=lambda t: t.rank)][:2] == [0, 1]
        assert min(queue.peek(), key=lambda t: t.rank).title == "Graduate Analyst"

    def test_a_page_is_hunted_once_a_search_in_its_hunt_cache(self, queue):
        redis = fakeredis.FakeRedis(decode_responses=True)
        fetched: list[str] = []
        who = hunt.Hunter(config=HuntConfig(), robots=AllowAll(), fetch=lambda u: fetched.append(u) or DRAWN, redis=redis, run="run-1")  # type: ignore[arg-type]  # fmt: skip
        listing = result("https://builtinlondon.uk/jobs?search=x")
        discover(FakeSerper({"Graduate": [listing, listing]}), queue, who)
        discover(FakeSerper({"Graduate": [listing]}), queue, who)
        assert len(fetched) == 1 and redis.smembers("wera:hunt:run-1") == {"https://builtinlondon.uk/jobs?search=x"}
        assert 0 < redis.ttl("wera:hunt:run-1") <= hunt.KEY_TTL_SECONDS


class TestAnAggregatorsCopy:
    COPY = result("https://uk.bebee.com/job/7c1d0f0a2b3e4c5d6e7f8a9b0c1d2e3f", "Graduate Software Engineer - Acme Robotics - London | beBee", "Acme Robotics is hiring")  # fmt: skip
    ORIGINAL = Result("https://job-boards.greenhouse.io/acmerobotics/jobs/4012345678", "Graduate Software Engineer", "Acme Robotics · London", None, 1)  # fmt: skip

    def test_is_traced_to_the_employers_own_posting(self, queue):
        lookup = FakeSerper({'"Acme Robotics"': [self.ORIGINAL]}, max_queries=5)
        who = hunt.Hunter(config=HuntConfig(), robots=AllowAll(), fetch=lambda _u: None, lookup=lambda: lookup)  # type: ignore[arg-type]
        out = discover(FakeSerper({"Graduate": [self.COPY]}), queue, who)
        assert [t.url for t in queue.peek()] == [self.ORIGINAL.url]
        assert lookup.asked[0].startswith('"Graduate Software Engineer" "Acme Robotics" -site:adzuna.co.uk')
        assert (out.hunted.copies, out.hunted.lookups, out.hunted.originals) == (1, 1, 1)

    def test_one_it_cant_trace_is_a_posting_as_before(self, queue):
        lookup = FakeSerper({}, max_queries=5)
        who = hunt.Hunter(config=HuntConfig(), robots=AllowAll(), fetch=lambda _u: None, lookup=lookup)  # type: ignore[arg-type]
        discover(FakeSerper({"Graduate": [self.COPY]}), queue, who)
        assert [t.url for t in queue.peek()] == [self.COPY.url]

    def test_past_the_lookups_cap_no_search_is_sent_and_it_is_a_preview(self, queue):
        """22 Nov: a copy the search's lookups didn't reach is a preview, read with its lookup when
        someone asks — no longer queued and read on the aggregator's page."""
        lookup = FakeSerper({'"Acme Robotics"': [self.ORIGINAL]}, max_queries=0)
        who = hunt.Hunter(config=HuntConfig(), robots=AllowAll(), fetch=lambda _u: None, lookup=lookup)  # type: ignore[arg-type]
        out = discover(FakeSerper({"Graduate": [self.COPY]}), queue, who)
        assert lookup.asked == [] and queue.peek() == []
        [preview] = out.previews
        assert (preview.feed, preview.country, preview.lead.title, preview.lead.company) == (None, "gb", "Graduate Software Engineer", "Acme Robotics")  # fmt: skip
        assert preview.found_on.site == "beBee" and preview.found_on.kind == "copy"

    def test_a_board_in_sites_yaml_is_not_an_aggregator(self):
        assert hunt.is_copy("https://uk.bebee.com/job/abc1234567", BOARDS)
        assert hunt.is_copy("https://uk.indeed.com/viewjob?jk=abc123", BOARDS)
        assert not hunt.is_copy("https://www.efinancialcareers.com/jobs-UK-London-Analyst.id22912345", BOARDS)
        assert not hunt.is_copy("https://builtinlondon.uk/job/x/12000001", BOARDS)

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Graduate Software Engineer - Acme Robotics - London | beBee", ("Graduate Software Engineer", "Acme Robotics")),
            ("Graduate Analyst job in London at Northwind Capital | JobLeads", ("Graduate Analyst", "Northwind Capital")),
            ("Junior Data Analyst - Contoso Ltd - Manchester - Indeed.com", ("Junior Data Analyst", "Contoso Ltd")),
            (
                "Graduate Software Engineer - London, England, United Kingdom - Acme | beBee",
                ("Graduate Software Engineer", "Acme"),
            ),
            ("1,204 Graduate Analyst jobs in London", None),
            ("Graduate Software Engineer - London - Indeed.com", None),
        ],
    )
    def test_its_title_and_company_are_read_off_the_result(self, title, expected):
        lead = hunt.copy_lead(result("https://uk.bebee.com/job/abc1234567", title))
        assert (lead.title, lead.company) == expected if expected else lead is None


class TestTheStepLog:
    def test_a_tier_says_what_hunting_its_pages_found_and_what_was_new(self, queue, firecrawl):
        """The run's log line for a tier ends "Hunted N pages (M rendered): K links to postings, J not seen
        before" — links, not jobs (22 Nov)."""
        from wera_agent.graphs import search_pipeline

        pages = {"https://builtinlondon.uk/jobs?search=x": SHELL, "https://builtinlondon.uk/jobs?c=grad": DRAWN}
        out = discover(
            FakeSerper({"Graduate": [result(u, n=i) for i, u in enumerate(pages, 1)]}),
            queue,
            hunter(pages.get),
            known={"https://builtinlondon.uk/job/graduate-data-engineer/12000002"},
        )
        note = search_pipeline._search_note(1, out)
        assert note.endswith(" · Hunted 2 pages (1 rendered): 4 links to postings, 1 not seen before"), note

    def test_refusals_and_robots_are_said_and_a_pass_with_no_page_says_nothing(self, queue):
        from wera_agent.graphs import search_pipeline

        def refuse(_url: str) -> str:
            raise hunt.Refused("HTTP 403")

        out = discover(FakeSerper({"Graduate": [result("https://builtinlondon.uk/jobs?search=x")]}), queue, hunter(refuse))
        assert search_pipeline._search_note(1, out).endswith(
            "Hunted 1 page (0 rendered): 0 links to postings, 0 not seen before (1 refused)"
        )
        assert search_pipeline._hunt_note(hunt.Hunted()) == ""


class TestWhatAHuntedLinkSaysAboutItself:
    """22 Nov: a hunted link has no company in hand. What its card says is only what is known —
    the employer an employer's job system's address names, or the one its text names — and an
    unknown employer is shown as the site it was found on, never guessed."""

    @pytest.mark.parametrize(
        ("url", "employer"),
        [
            ("https://job-boards.greenhouse.io/acmerobotics/jobs/4012345678", "Acmerobotics"),
            ("https://jobs.lever.co/northwind-capital/0b5c1d2e-3f4a-4b5c-8d9e-0f1a2b3c4d5e", "Northwind Capital"),
            ("https://jobs.ashbyhq.com/partly.com/0b5c1d2e-3f4a-4b5c-8d9e-0f1a2b3c4d5e", "Partly"),
            ("https://contoso.wd3.myworkdayjobs.com/en-US/careers/job/London/Graduate-Analyst_R12345", "Contoso"),
            ("https://jobs.smartrecruiters.com/FabrikamGroup/743999912345678", "FabrikamGroup"),
            ("https://apply.workable.com/tailspin-toys/j/ABC1234567/", "Tailspin Toys"),
            ("https://www.totaljobs.com/job/graduate-analyst/acme-job12000001", None),
            ("https://builtinlondon.uk/job/graduate-swe/12000001", None),
        ],
    )
    def test_the_employer_an_address_names(self, url, employer):
        assert hunt.ats_employer(url) == employer

    def test_the_employer_a_link_names_and_nothing_invented(self):
        url = "https://www.totaljobs.com/job/graduate-analyst/12000001"
        assert hunt.employer_of(url, "Graduate Analyst at Northwind Capital") == ("Graduate Analyst", "Northwind Capital")
        assert hunt.employer_of(url, "Graduate Analyst") == ("Graduate Analyst", None)
        assert hunt.employer_of(url, "Graduate Analyst at London") == ("Graduate Analyst at London", None)  # a place
        ats = "https://jobs.lever.co/northwind-capital/0b5c1d2e-3f4a-4b5c-8d9e-0f1a2b3c4d5e"
        assert hunt.employer_of(ats, "Graduate Analyst") == ("Graduate Analyst", "Northwind Capital")

    def test_a_sites_name(self):
        assert hunt.site_name("https://builtinlondon.uk/jobs?search=x", BOARDS) == "Built In London"
        assert hunt.site_name("https://uk.bebee.com/job/abc") == "beBee"
        assert hunt.site_name("https://www.totaljobs.com/jobs/graduate") == "Totaljobs"
        assert hunt.site_name("https://www.reed.co.uk/jobs/graduate-jobs") == "Reed"
        assert hunt.site_name("https://jobs.example-board.com/search") == "Example-board"


class TestTheCopiesShareTheSearchsLookups:
    """22 Nov: an aggregator's copy takes a lookup from the search's one allowance, best match
    first and only at the bar or above; the rest are previews."""

    COPIES = [
        result("https://uk.bebee.com/job/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "Graduate Analyst - Weak Co - London | beBee"),
        result("https://uk.bebee.com/job/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "Graduate Analyst - Best Co - London | beBee"),
        result("https://uk.bebee.com/job/cccccccccccccccccccccccccccccccc", "Graduate Analyst - Under Co - London | beBee"),
    ]

    def test_best_match_first_and_none_under_the_bar(self):
        lookup = FakeSerper({}, max_queries=1)
        who = hunt.Hunter(config=HuntConfig(), robots=AllowAll(), fetch=lambda _u: None, lookup=lookup)  # type: ignore[arg-type]
        what, did = who.originals([(c, "gb", "en", []) for c in self.COPIES], sites=BOARDS, scores=[0.75, 0.95, 0.5], bar=0.72)
        assert [w for w, _hit in what] == ["waiting", "none", "waiting"]  # the best took the one lookup
        assert len(lookup.asked) == 1 and '"Best Co"' in lookup.asked[0] and did.lookups == 1

    def test_the_ones_it_didnt_reach_are_previews_best_first(self):
        lookup = FakeSerper({}, max_queries=0)
        who = hunt.Hunter(config=HuntConfig(), robots=AllowAll(), fetch=lambda _u: None, lookup=lookup)  # type: ignore[arg-type]
        scores = {"Weak Co": 0.75, "Best Co": 0.95, "Under Co": 0.5}

        def rank(items):
            return [next(v for k, v in scores.items() if k in f"{t} {s}") if any(k in f"{t} {s}" for k in scores) else 0.0 for t, s in items]  # fmt: skip

        q = TargetQueue(fakeredis.FakeRedis(decode_responses=True), "5-abc", rank=rank)
        out = discover(FakeSerper({"Graduate": self.COPIES}), q, who)
        assert lookup.asked == [] and q.peek() == []
        assert [p.lead.company for p in out.previews] == ["Best Co", "Weak Co", "Under Co"]
        assert all(p.feed is None and p.found_on.kind == "copy" for p in out.previews)


class TestABoardIsNeverTheEmployer:
    """22 Nov: a hunted link whose employer nothing named is saved under its site's name, and a
    board's posting can name the board as its organisation; neither is who a letter goes to."""

    @pytest.mark.parametrize("name", ["Gradcracker", "Built In London", "Totaljobs", "beBee", "Milkround", "JobLeads"])
    def test_the_boards_by_name(self, name):
        from wera_agent.domain import dedupe

        assert dedupe.is_intermediary(name)

    @pytest.mark.parametrize("name", ["Acme Robotics", "Citi", "Larkspur Payments", "Northwind Capital"])
    def test_an_employer_is_not_one(self, name):
        from wera_agent.domain import dedupe

        assert not dedupe.is_intermediary(name)

    def test_a_read_hunted_preview_that_names_only_its_board_writes_to_no_employer(self):
        from wera_agent.nodes import tailor
        from wera_agent.sources import normalise

        job = normalise.from_hunted("https://www.gradcracker.com/hub/123/acme/graduate-job/4567/software-engineer",
                                    title="Graduate Software Engineer", employer=None, site="Gradcracker", listing=None, now=0)  # fmt: skip
        assert job.company == "Gradcracker" and not job.employer_named
        assert "posted by Gradcracker — a job board or agency, not the employer" in tailor.job_header(SimpleNamespace(job=job))  # type: ignore[arg-type]
