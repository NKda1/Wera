"""SRCH-26, 18 Nov: a search that balances itself, best match first.

On 18 Nov, 9 of the 16 jobs left on the list were one agency's London reposts through Reed, and a
country with weaker matches could get none of a search's reads. Now: the best match first, within a
floor for each ticked country and a ceiling for each source; APIs, Google and Firecrawl each with a
job; and the pool of public postings shared by an industry, ranked against each reader's own CV.
"""

import fakeredis
import pytest

from wera_agent.config import wera
from wera_agent.sources import firecrawl_search
from wera_agent.sources.firecrawl_search import FirecrawlSearch, SearchCaps, SearchOutcome
from wera_agent.sources.provider_guard import Guard
from wera_agent.sources.targets import Balance, Target, TargetQueue, source_of


def posting(n: int, *, country: str, via: str | None = None, title: str = "Software Engineer", query: str = "q") -> Target:
    return Target(
        f"https://example.com/{country}/{via or 'web'}/{n}",
        "example.com",
        "feed" if via else "open",
        query,
        n,
        title,
        "",
        None,
        via=via,
        country=country,
    )


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


class TestTheShares:
    def test_each_ticked_country_gets_its_floor_even_with_weaker_matches(self):
        """The UK's matches are all better, but Switzerland still gets its share of the 12."""
        ordered = [*(posting(i, country="gb") for i in range(12)), *(posting(i, country="ch") for i in range(12))]
        balance = Balance(["ch", "gb"], per_run=12, ceiling=1.0)
        chosen = balance.choose(ordered, 12)
        assert sum(t.country == "ch" for t in chosen) >= 6

    def test_no_source_takes_more_than_its_ceiling_while_others_have_postings(self):
        """18 Nov: one agency's Reed reposts were 9 of 16. Reed's matches are all better here, but
        three sources each keep to 40% of the 12."""
        ordered = [
            *(posting(i, country="gb", via="reed") for i in range(10)),
            *(posting(i, country="gb") for i in range(10)),
            *(posting(i, country="gb", via="adzuna") for i in range(10)),
        ]
        chosen = Balance(["gb"], per_run=12, ceiling=0.4).choose(ordered, 12)
        counts = {s: sum(source_of(t) == s for t in chosen) for s in ("reed", "google", "adzuna")}
        assert counts == {"reed": 4, "google": 4, "adzuna": 4}

    def test_two_sources_take_turns_when_both_reach_the_ceiling(self):
        ordered = [*(posting(i, country="gb", via="reed") for i in range(10)), *(posting(i, country="gb") for i in range(10))]
        chosen = Balance(["gb"], per_run=12, ceiling=0.4).choose(ordered, 12)
        assert sum(source_of(t) == "reed" for t in chosen) == 6

    def test_a_source_with_nothing_better_gives_up_its_ceiling(self):
        """Every posting from one API: the slots are filled rather than left empty."""
        ordered = [posting(i, country="gb", via="reed") for i in range(12)]
        assert len(Balance(["gb"], per_run=12, ceiling=0.4).choose(ordered, 12)) == 12

    def test_one_country_and_one_source_is_plain_best_match(self):
        ordered = [posting(i, country="gb") for i in range(6)]
        assert Balance(["gb"], per_run=12).choose(ordered, 4) == ordered[:4]

    def test_firecrawls_finds_are_their_own_source(self):
        assert source_of(posting(1, country="gb", query="firecrawl: site:x q")) == "firecrawl"
        assert source_of(posting(1, country="gb")) == "google"
        assert source_of(posting(1, country="gb", via="adzuna")) == "adzuna"

    def test_the_split_is_said(self):
        balance = Balance(["ch", "gb"], per_run=4)
        balance.choose([posting(1, country="gb", via="reed"), posting(2, country="ch")], 2)
        assert balance.split() in (
            "gb 1, ch 1 · reed 1, google 1",
            "ch 1, gb 1 · google 1, reed 1",
            "gb 1, ch 1 · google 1, reed 1",
            "ch 1, gb 1 · reed 1, google 1",
        )


class TestASharedPool:
    def test_two_readers_rank_one_pool_by_their_own_cvs(self, r):
        """The pool holds postings only; each reader's own rank decides their order, and what one
        reads stays in the pool for the other."""
        pool = TargetQueue(r, "plan-a", pool="backend-engineering:graduate")
        pool.push([posting(1, country="gb", title="Data Engineer"), posting(2, country="gb", title="Frontend Engineer")])
        likes_data = TargetQueue(r, "plan-a", pool="backend-engineering:graduate", reader="a",
                                 rank=lambda items: [1.0 if "Data" in t else 0.0 for t, _ in items])  # fmt: skip
        likes_frontend = TargetQueue(r, "plan-b", pool="backend-engineering:graduate", reader="b",
                                     rank=lambda items: [1.0 if "Frontend" in t else 0.0 for t, _ in items])  # fmt: skip
        assert likes_data.take(1)[0].title == "Data Engineer"
        assert likes_frontend.take(1)[0].title == "Frontend Engineer"
        assert likes_data.size() == 1 and likes_frontend.size() == 1  # each has one left to read
        assert int(r.zcard("wera:pool:backend-engineering:graduate")) == 2  # and the pool is whole

    def test_a_put_back_posting_is_read_again(self, r):
        q = TargetQueue(r, "p", pool="x:any", reader="a")
        q.push([posting(1, country="gb")])
        taken = q.take(1)
        assert q.size() == 0
        q.put_back(taken)
        assert q.size() == 1

    def test_a_new_plan_never_retires_a_shared_pool(self, r):
        TargetQueue(r, "p", pool="backend:graduate", reader="a").push([posting(1, country="gb")])
        TargetQueue(r, "q", pool="data:graduate", reader="a").retire_others()
        assert int(r.zcard("wera:pool:backend:graduate")) == 1


class TestFirecrawlAsTheStandIn:
    class FakeClient:
        def __init__(self):
            self.asked: list[dict] = []

        def search(self, query, **kw):
            self.asked.append({"q": query, **kw})
            item = type("Item", (), {"url": "https://jobs.example.ch/1", "title": "Junior Developer", "description": "Zürich"})
            return type("Data", (), {"web": [item]})

    def test_it_answers_like_serper_and_charges_the_run(self, monkeypatch):
        client = self.FakeClient()
        monkeypatch.setattr(firecrawl_search, "_client", lambda: client)
        spend = SearchOutcome()
        searcher = FirecrawlSearch(max_queries=2, spend=spend, caps=SearchCaps(max_credits=100))
        results = searcher.search('"Junior Developer" job in "Zürich"', gl="ch", hl="en")
        assert results[0].url == "https://jobs.example.ch/1" and results[0].position == 1
        assert client.asked[0]["country"] == "CH"
        assert spend.credits_used == firecrawl_search.SEARCH_CREDITS

    def test_it_stops_at_its_own_limit_and_the_runs_credits(self, monkeypatch):
        monkeypatch.setattr(firecrawl_search, "_client", lambda: self.FakeClient())
        capped = FirecrawlSearch(max_queries=1, spend=SearchOutcome(), caps=SearchCaps(max_credits=100))
        capped.search("a", gl="gb", hl="en")
        assert capped.search("b", gl="gb", hl="en") is None and capped.stopped == "cap"
        broke = FirecrawlSearch(max_queries=5, spend=SearchOutcome(credits_used=99), caps=SearchCaps(max_credits=100))
        assert broke.search("a", gl="gb", hl="en") is None and broke.stopped == "credits"


class TestTheSettings:
    def test_seventy_a_day(self):
        assert wera().max_scored_per_day == 70

    def test_an_api_answer_is_kept_for_a_day_and_serpers_for_six_hours(self, r):
        Guard(r, "adzuna").remember("adzuna:k", {"x": 1})
        Guard(r, "serper").remember("serper:k", {"x": 1})
        assert 23 * 3600 < r.ttl("wera:answer:adzuna:k") <= 24 * 3600
        assert 5 * 3600 < r.ttl("wera:answer:serper:k") <= 6 * 3600
