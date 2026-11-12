"""SRCH-20, stage 3: the Harvester reads queued postings and checks them (SRCH-23).

What it must hold to: nothing that wasn't classified a posting is read; free rungs come first and
Firecrawl last; what is stored as the description is the posting's own text; and a posting that
fails a check is refused, remembered for a week, and never saved.
"""

from types import SimpleNamespace

import fakeredis
import pytest

from wera_agent.clock import iso
from wera_agent.config import Site
from wera_agent.sources import extract, firecrawl_search, harvest
from wera_agent.sources.firecrawl_search import SearchCaps, SearchOutcome
from wera_agent.sources.targets import Target, TargetQueue

NOW = 1_762_850_000_000
DAY = 24 * 60 * 60 * 1000
BOARDS = [Site(domain="builtinlondon.uk", name="Built In London", terms_reviewed_on="2026-01-01", countries=["GB"])]
URL = "https://builtinlondon.uk/job/graduate-software-engineer/12138050"
DESCRIPTION = "You will build and run backend services in Python for our payments platform. " * 6


def posting_page(*, posted: str | None = None, description: str = DESCRIPTION) -> str:
    date = f',"datePosted":"{posted}"' if posted else ""
    return (
        '<html><head><script type="application/ld+json">'
        '{"@type":"JobPosting","title":"Graduate Software Engineer","hiringOrganization":{"name":"Nominal"}'
        f',"description":"{description}"{date}}}'
        "</script></head><body></body></html>"
    )


def target(url: str = URL, title: str = "Graduate Software Engineer") -> Target:
    return Target(url=url, host="builtinlondon.uk", source="board", query="q", rank=0, title=title, snippet="", date=None)


class AllowAll:
    def allows(self, _url: str) -> bool:
        return True


@pytest.fixture
def queue():
    return TargetQueue(fakeredis.FakeRedis(decode_responses=True), "5-abc")


def read(targets: list[Target], queue: TargetQueue, *, caps: SearchCaps | None = None, robots=None, **kw):
    spend = SearchOutcome()
    out = harvest.harvest(
        targets, caps=caps or SearchCaps(), spend=spend, now=NOW, sites=BOARDS, queue=queue, robots=robots or AllowAll(), **kw
    )
    return out, spend


@pytest.fixture
def no_firecrawl(monkeypatch):
    monkeypatch.setattr(firecrawl_search, "_client", lambda: pytest.fail("Firecrawl was asked"))


class TestTheGate:
    def test_a_url_that_isnt_a_posting_is_never_read(self, queue, monkeypatch, no_firecrawl):
        """The drift guard: whatever put it in the queue, a results page is not read as a job."""
        monkeypatch.setattr(extract, "_get", lambda _u: pytest.fail("a listing was fetched"))
        out, _ = read([target("https://www.gradcracker.com/search/technology-it/graduate-jobs?page=2")], queue)
        assert out.jobs == [] and out.rejected == {"not a posting": 1}

    def test_robots_is_asked_first(self, queue, monkeypatch, no_firecrawl):
        class DenyAll:
            def allows(self, _url: str) -> bool:
                return False

        monkeypatch.setattr(extract, "_get", lambda _u: pytest.fail("a disallowed page was fetched"))
        out, _ = read([target()], queue, robots=DenyAll())
        assert out.rejected == {"robots.txt disallows": 1}


class TestReading:
    def test_free_rungs_first_and_no_credits(self, queue, monkeypatch, no_firecrawl):
        monkeypatch.setattr(extract, "_get", lambda _u: posting_page(posted=iso(NOW - 3 * DAY)))
        out, spend = read([target()], queue)
        assert [j.title for j in out.jobs] == ["Graduate Software Engineer"]
        assert out.by_path == {"json-ld": 1} and spend.credits_used == 0

    def test_a_react_page_is_read_by_the_browser_rung_for_free(self, queue, monkeypatch, no_firecrawl):
        monkeypatch.setattr(extract, "_get", lambda _u: "<html><body><div id=root></div></body></html>")
        out, spend = read([target()], queue, fetch_html=lambda _u: posting_page())
        assert out.by_path == {"json-ld-rendered": 1} and spend.credits_used == 0

    def test_firecrawl_last_and_its_markdown_is_the_description(self, queue, monkeypatch):
        """The JSON format's `description` is Firecrawl's summary; the stored description must be
        the posting's own words, which every later prompt quotes (LTR-02)."""
        monkeypatch.setattr(extract, "_get", lambda _u: None)
        page = SimpleNamespace(
            json={"title": "Graduate Software Engineer", "company": "Nominal", "description": "A short summary."},
            markdown="# Graduate Software Engineer\n\n" + DESCRIPTION,
        )
        monkeypatch.setattr(firecrawl_search, "_client", lambda: SimpleNamespace(scrape=lambda *a, **k: page))
        out, spend = read([target()], queue)
        assert out.by_path == {"firecrawl": 1} and spend.credits_used == firecrawl_search.JSON_SCRAPE_CREDITS
        assert out.jobs[0].description_md.startswith("# Graduate Software Engineer")
        assert "A short summary." not in out.jobs[0].description_md

    def test_a_posting_left_unread_by_a_cap_goes_back_to_the_front(self, queue, monkeypatch, no_firecrawl):
        monkeypatch.setattr(extract, "_get", lambda _u: None)
        first, second = target(), target("https://builtinlondon.uk/job/backend-engineer/12000001")
        queue.push([target("https://builtinlondon.uk/job/later/12999999")])
        out, _ = read([first, second], queue, caps=SearchCaps(max_pages=0))
        assert out.put_back == 2 and out.rejected == {}
        assert [t.url for t in queue.take(3)] == [first.url, second.url, "https://builtinlondon.uk/job/later/12999999"]


class TestTheChecks:
    """SRCH-23 and SRCH-24, each refused before anything is saved, and remembered for a week."""

    @pytest.mark.parametrize(
        ("page", "reason"),
        [
            (posting_page(description="Apply now."), "description too short"),
            # 23, not 22: a board's date is approximate (MATCH-08) and gets a day of slack.
            (posting_page(posted=iso(NOW - 23 * DAY)), "older than 21 days"),
            (posting_page(description="This vacancy has expired. " + DESCRIPTION), "closed"),
        ],
    )
    def test_refused_and_remembered(self, queue, monkeypatch, no_firecrawl, page, reason):
        monkeypatch.setattr(extract, "_get", lambda _u: page)
        out, _ = read([target()], queue)
        assert out.jobs == [] and out.rejected == {reason: 1}
        assert queue.is_rejected(URL)  # not queued again for a week

    def test_an_undated_posting_is_kept(self, queue, monkeypatch, no_firecrawl):
        monkeypatch.setattr(extract, "_get", lambda _u: posting_page())
        out, _ = read([target()], queue)
        assert len(out.jobs) == 1 and out.jobs[0].posted_at is None

    def test_a_page_of_several_postings_is_refused_as_a_listing(self, queue, monkeypatch, no_firecrawl):
        listing = posting_page().replace("</script>", "</script>" + posting_page().split("<head>")[1].split("</head>")[0])
        listing = listing.replace('"Nominal"', '"Nominal"', 1).replace('"Graduate Software Engineer"', '"Backend Engineer"', 1)
        monkeypatch.setattr(extract, "_get", lambda _u: listing)
        out, _ = read([target()], queue)
        assert out.rejected == {"listing": 1}
