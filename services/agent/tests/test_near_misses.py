"""UPSK-06 (20 Nov): the jobs a CV nearly matched feed Upskills.

A finance search found 606 postings and the title rule dropped 274 unread — "Equity Analyst" and
"Commercial Finance Analyst" among them. What those ask for is what a person a step from their
targets should learn. What matters: only a title drop is a near miss (never an excluded word or a
too-senior title); they are similarity-gated and capped; a posting is read once; and what they ask
reaches Upskills with its own count, at half weight, beside the jobs on the list.
"""

from datetime import date

import fakeredis
import httpx
import pytest
import respx
from sqlalchemy import func, select

from wera_agent.clock import iso, now_ms
from wera_agent.config import FeedConfig, wera
from wera_agent.db.models import JobSignalRow
from wera_agent.db.session import transaction
from wera_agent.domain import filters
from wera_agent.nodes import fit, prefilter, strategist
from wera_agent.services import near_misses, upskills
from wera_agent.services.store import Store
from wera_agent.sources.feeds import adzuna, reed
from wera_agent.sources.targets import TargetQueue

from .test_feeds import NOW, FakeSerper, fixture, planned
from .test_feeds import keys as keys  # a fixture, used by name
from .test_upskills import job, scored


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


def candidate(i: int, score: float, source: str = "lead") -> near_misses.Candidate:
    return near_misses.Candidate(
        f"k{i}", f"https://x.example/{i}", f"Equity Analyst {i}", f"Co {i}", "Valuation work.", source, score=score
    )


class TestWhatCountsAsANearMiss:
    @pytest.mark.usefixtures("keys")
    @respx.mock(assert_all_called=False)
    def test_only_a_title_drop_is_kept_never_an_excluded_word_or_a_senior_title(self, redis, store, respx_mock, monkeypatch):
        monkeypatch.setitem(wera().feeds, "reed", FeedConfig(terms_reviewed_on=date(2026, 11, 13), max_searches=3))
        base = fixture("reed_search")["results"][0]
        titles = [
            "Equity Research Associate",
            "Senior Software Engineer",
            "Head of Platform Engineering",
            "Graduate Software Engineer",
        ]
        rows = [
            {**base, "jobId": i, "jobTitle": t, "date": "10/11/2026", "jobUrl": f"https://www.reed.co.uk/jobs/x/{i}"}
            for i, t in enumerate(titles, 1)
        ]  # fmt: skip
        respx_mock.get(reed.SEARCH).mock(return_value=httpx.Response(200, json={"results": rows, "totalResults": 4}))
        respx_mock.get(adzuna.SEARCH.format(country="gb")).mock(return_value=httpx.Response(200, json={"results": []}))
        profile = store.get_profile().model_copy(
            update={
                "titles": ["Graduate Software Engineer"],
                "locations": ["London, UK"],
                "seniority": ["graduate"],
                "exclude": ["Senior"],
            }
        )
        found = strategist.discover_feeds(planned(), profile, queue=TargetQueue(redis, "plan"), known_urls=set(), sites=[], now=NOW, redis=redis, resource=FakeSerper({}))  # fmt: skip
        assert [n.title for n in found.near_misses] == ["Equity Research Associate"]
        assert found.dropped == {"title": 1, "term": 1, "senior": 1}
        assert (found.unique, found.passed) == (4, 1)


class TestWhichAreRead:
    def test_below_the_floor_is_never_read_and_the_best_come_first(self):
        chosen, above, _ = near_misses.choose(
            [candidate(1, 0.6), candidate(2, 0.9), candidate(3, 0.75)], rank=lambda xs: [], stored=set(), floor=0.7, cap=40
        )
        assert [c.key for c in chosen] == ["k2", "k3"] and above == 2

    def test_capped_per_search(self):
        chosen, above, _ = near_misses.choose(
            [candidate(i, 0.8 + i / 100) for i in range(10)], rank=lambda xs: [], stored=set(), floor=0.7, cap=4
        )
        assert [c.key for c in chosen] == ["k9", "k8", "k7", "k6"] and above == 10

    def test_a_posting_already_read_is_not_read_again(self):
        chosen, _above, again = near_misses.choose(
            [candidate(1, 0.9), candidate(2, 0.8)], rank=lambda xs: [], stored={"k1"}, floor=0.7, cap=40
        )
        assert [c.key for c in chosen] == ["k2"] and again == 1

    def test_without_a_ranker_nothing_is_chosen(self):
        assert near_misses.choose([candidate(1, 0.9)], rank=None, stored=set(), floor=0.7, cap=40)[0] == []

    def test_unscored_candidates_are_ranked(self):
        c = candidate(1, 0.0)
        c.score = None
        chosen, _, _ = near_misses.choose([c], rank=lambda xs: [0.95] * len(xs), stored=set(), floor=0.7, cap=40)
        assert chosen and chosen[0].score == 0.95


class TestCapture:
    def test_each_is_extracted_once_and_stored(self, client, monkeypatch):
        calls = []
        real = fit.keywords_for

        def counting(session, j, cv):
            calls.append(j.title)
            return real(session, j, cv)

        monkeypatch.setattr(fit, "keywords_for", counting)
        lead = strategist.NearMiss(
            "https://www.adzuna.co.uk/x/1", "Equity Analyst", "Barings", "Excel and Python for valuation.", "adzuna", 0.9
        )
        with transaction() as session:
            cv = Store(session).cv()
        first = near_misses.capture([lead], cv=cv, rank=lambda xs: [0.1] * len(xs), now=now_ms())
        second = near_misses.capture([lead], cv=cv, rank=lambda xs: [0.1] * len(xs), now=now_ms())
        assert (first.extracted, second.extracted, second.already_read) == (1, 0, 1)
        assert calls == ["Equity Analyst"]
        with transaction() as session:
            row = session.get(JobSignalRow, near_misses.key_for(lead.url, ""))
            assert (row.title, row.company, row.source, row.similarity) == ("Equity Analyst", "Barings", "lead", 0.9)

    def test_off_reads_nothing(self, client, monkeypatch):
        monkeypatch.setattr(wera().upskills, "near_miss_per_search", 0)
        lead = strategist.NearMiss("https://x.example/1", "Equity Analyst", "Barings", "Excel.", "adzuna", 0.9)
        with transaction() as session:
            cv = Store(session).cv()
            assert near_misses.capture([lead], cv=cv, rank=lambda xs: [], now=now_ms()).extracted == 0
            assert session.scalar(select(func.count()).select_from(JobSignalRow)) == 0


def signal(session, key: str, *terms: tuple[str, str], job_id: str | None = None, seen: int | None = None) -> None:
    session.add(
        JobSignalRow(
            key=key, url=f"https://x.example/{key}", job_id=job_id, title=f"Equity Analyst {key}", company=f"Firm {key}",
            source="lead", similarity=0.8, keywords=[{"term": t, "weight": w, "category": "tool"} for t, w in terms],
            education=None, job_hash=None, seen_at=iso(seen or now_ms()),
        )
    )  # fmt: skip
    session.flush()


class TestUpskillsReadsThem:
    def test_a_near_miss_keyword_reaches_the_gaps_with_its_own_count(self, store):
        scored(store, job(1), ("Kafka", "required"))
        for k in ("a", "b", "c"):
            signal(store.s, k, ("Kafka", "required"), ("Bloomberg Terminal", "nice"))
        report = upskills.report(store.s, store.cv(), now=now_ms())
        listed = {u.term: u for u in report.missing}
        assert "Bloomberg Terminal" not in listed and listed["Kafka"].near_misses == 0
        view = report.with_near_misses
        assert view is not None and view.near_misses == 3
        both = {u.term: u for u in view.missing}
        assert (both["Kafka"].jobs, both["Kafka"].near_misses) == (1, 3)
        assert (both["Bloomberg Terminal"].jobs, both["Bloomberg Terminal"].near_misses) == (0, 3)
        assert [e.company for e in both["Kafka"].near_miss_examples] == ["Firm a", "Firm b", "Firm c"]

    def test_a_near_miss_weighs_half_a_job_on_the_list(self, store):
        scored(store, job(2), ("Snowflake", "required"))
        signal(store.s, "d", ("Kafka", "required"))
        signal(store.s, "e", ("Kafka", "required"))
        both = {u.term: u for u in upskills.report(store.s, store.cv(), now=now_ms()).with_near_misses.missing}
        # 20 Nov: each also weighs its lift (`upskills.lift`): the scored job's fit, the near
        # misses' similarity (0.8 here). Two near misses × required 2 × 0.5 × lift.
        near = upskills.lift(0.8, strong=upskills.STRONG_SIMILARITY, weak=upskills.WEAK_SIMILARITY)
        assert both["Kafka"].score == pytest.approx(2 * 2 * 0.5 * near)
        assert both["Snowflake"].score == pytest.approx(2 * upskills.lift(4.6 / 10))  # its fit total is 4.6

    def test_one_scored_since_counts_once(self, store):
        scored(store, job(3), ("Kafka", "required"))
        signal(store.s, "f", ("Kafka", "required"), job_id="job-up-3")
        report = upskills.report(store.s, store.cv(), now=now_ms())
        assert report.with_near_misses is None  # the only near miss is a job on the list now

    def test_none_read_means_no_near_miss_view(self, store):
        scored(store, job(4), ("Kafka", "required"))
        assert upskills.report(store.s, store.cv(), now=now_ms()).with_near_misses is None


class TestTitlesByMeaning:
    """M5 (20 Nov): a title as close as `TITLE_MEANING_MIN` to a target is one of the targets."""

    def test_a_close_title_is_one_of_the_targets(self, store, monkeypatch):
        profile = store.get_profile().model_copy(update={"titles": ["Equity Research Analyst"], "exclude": ["Senior"]})
        monkeypatch.setattr(prefilter, "title_closeness", lambda title, targets: 0.9 if "Equity" in title else 0.3)
        assert filters.title_mismatch("Equity Analyst", profile) is None
        assert filters.title_mismatch("Sales Executive", profile) is not None

    def test_excluded_words_and_seniority_are_unchanged(self, store, monkeypatch):
        profile = store.get_profile().model_copy(
            update={"titles": ["Equity Research Analyst"], "exclude": ["Senior"], "seniority": ["graduate"]}
        )
        monkeypatch.setattr(prefilter, "title_closeness", lambda title, targets: 0.99)
        assert strategist._why_excluded("Senior Equity Analyst", profile) == "term"
        assert strategist._why_excluded("Head of Equity Research", profile) == "senior"

    def test_the_demo_embedder_says_nothing_about_meaning(self):
        assert prefilter.title_closeness("Equity Analyst", ["Equity Research Analyst"]) is None


class TestTheWholeAdWhenAnApiHasIt:
    """20 Nov: 23 of 40 snippets gave no keyword at all; a full-text API's lead is read in full."""

    def test_a_usajobs_card_is_read_whole(self):
        card = {"description": "Analyse budgets and build financial models in Excel and SQL. " * 5}
        c = near_misses.from_leads(
            [strategist.NearMiss("https://x.example/1", "Budget Analyst", "Treasury", "", "usajobs", 0.8, {"card": card})]
        )[0]
        assert near_misses.full_text(c, None) == card["description"].strip()

    def test_a_snippet_lead_has_no_whole_ad(self):
        c = near_misses.from_leads(
            [strategist.NearMiss("https://x.example/2", "Equity Analyst", "Barings", "Valuation.", "adzuna", 0.8)]
        )[0]
        assert near_misses.full_text(c, None) is None


class TestASearchNeverWaitsForUpskills:
    """20 Nov: read inside the run, near misses held a live search 31–40 s after its last job was
    scored, and "Find more" 32 s to read three. On a real install they are read on their own thread."""

    def test_on_postgres_the_search_returns_before_they_are_read(self, client, monkeypatch):
        import threading
        from types import SimpleNamespace

        from wera_agent.graphs import search_pipeline
        from wera_agent.settings import get_settings

        monkeypatch.setattr(search_pipeline, "db_engine", lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
        monkeypatch.setattr(get_settings(), "wera_demo_mode", False)
        release, ran_on = threading.Event(), []

        def slow_capture(*_a, **_k):
            ran_on.append(threading.current_thread().name)
            release.wait(5)
            return near_misses.Captured()

        monkeypatch.setattr(near_misses, "capture", slow_capture)
        out = search_pipeline.Piped()
        search_pipeline._near_misses(out, {}, [], cv=None, rank=None, now=0, at=0.9)
        assert out.near_misses is None  # returned without waiting
        release.set()
        for _ in range(50):
            if ran_on:
                break
            threading.Event().wait(0.05)
        assert ran_on == ["near-misses"]
