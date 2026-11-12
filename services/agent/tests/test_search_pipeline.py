"""SRCH-20 and SRCH-26: one pipeline, in one order, stopping at 8–12 jobs.

Serper and Redis are replaced (a table of answers, fakeredis) and so is the model that scores, but
the classifier, the queue, the Harvester and its checks are the real ones: what is under test is
the order the stages run in and when the pipeline stops — which is where it could drift.
"""

import ast
from pathlib import Path

import fakeredis
import pytest

from wera_agent.config import wera
from wera_agent.db.models import IntakeRunRow
from wera_agent.db.session import transaction
from wera_agent.errors import ApiError
from wera_agent.graphs import intake, search_pipeline
from wera_agent.services.store import Store
from wera_agent.settings import get_settings
from wera_agent.sources import extract, firecrawl_search, harvest
from wera_agent.sources.firecrawl_search import SearchCaps
from wera_agent.sources.serper import Result
from wera_agent.sources.targets import Target, TargetQueue

DESCRIPTION = "You will build and run backend services in Python for our payments platform. " * 6


def posting(i: int) -> str:
    return f"https://builtinlondon.uk/job/role-{i}/{1000000 + i}"


def page(url: str) -> str:
    n = url.rsplit("/", 1)[-1]
    return (
        '<html><head><script type="application/ld+json">'
        f'{{"@type":"JobPosting","title":"Graduate Software Engineer {n}","hiringOrganization":{{"name":"Company {n}"}},'
        f'"description":"{DESCRIPTION}"}}'
        "</script></head><body></body></html>"
    )


class FakeSerper:
    """Serper's interface, answering each tier's first query from a table."""

    instances: list["FakeSerper"] = []

    def __init__(self, *, max_queries: int, answers: dict[int, list[str]], planned):
        self.max_queries, self.sent, self.answered, self.errors, self.stopped = max_queries, 0, 0, [], None
        self.asked: list[str] = []
        firsts = {}
        for q in planned:
            firsts.setdefault(q.tier, q.q)
        self.table = {firsts[t]: urls for t, urls in answers.items() if t in firsts}

    @property
    def credits_used(self) -> int:
        return self.answered

    def search(self, q: str, *, gl: str, hl: str, page: int = 1):
        if self.sent >= self.max_queries:
            self.stopped = "cap"
            return None
        self.asked.append(q)
        self.sent += 1
        self.answered += 1
        return [
            Result(url=u, title="Graduate Software Engineer", snippet="", date=None, position=i + 1)
            for i, u in enumerate(self.table.get(q, []))
        ]


class AllowAll:
    def allows(self, _url: str) -> bool:
        return True


def no_plan(_store) -> None:
    """The Strategist's model isn't here; the queries are built from the CV and profile alone."""
    raise ApiError(424, "LLM_KEY_MISSING", "No model in this test.")


@pytest.fixture
def world(client, outbound, monkeypatch):
    """A real (not demo) run, with Serper, Redis, robots and the scoring model replaced."""
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    get_settings.cache_clear()
    redis = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(search_pipeline.targets, "connect", lambda: redis)
    monkeypatch.setattr(harvest, "Robots", AllowAll)
    monkeypatch.setattr(firecrawl_search, "_client", lambda: pytest.fail("Firecrawl was asked"))
    events: list[str] = []
    read: list[str] = []

    def get(url: str) -> str:
        events.append("harvest")
        read.append(url)
        return page(url)

    monkeypatch.setattr(extract, "_get", get)

    def judge(jobs, *, now, run_ids, progress_from=0.0, progress_to=1.0, limit=None, only_these=False):
        events.append("score")
        taken = list(jobs)[: limit if limit is not None else None]
        ids = {j.id for j in taken}
        return intake.Judged(surfaced=len(taken), new_ids=ids, visible_new=ids)

    monkeypatch.setattr(intake, "judge", judge)

    class World:
        def __init__(self):
            self.redis, self.events, self.read, self.serper = redis, events, read, None

        def answers(self, by_tier: dict[int, list[str]]) -> None:
            def make(*, max_queries: int):
                with transaction() as session:
                    planned, _ = Store(session).search_queries(refresh=False)
                real_search = FakeSerper(max_queries=max_queries, answers=by_tier, planned=planned)
                original = real_search.search

                def search(q, *, gl, hl, page=1):
                    events.append("discover")
                    return original(q, gl=gl, hl=hl, page=page)

                real_search.search = search
                self.serper = real_search
                return real_search

            monkeypatch.setattr(search_pipeline, "Serper", make)

        def queue(self) -> TargetQueue:
            with transaction() as session:
                return TargetQueue(redis, Store(session).plan_key())

        def run(self, mode: str = "search"):
            return search_pipeline.run({}, now=1_762_850_000_000, caps=SearchCaps(), mode=mode)

    monkeypatch.setattr(Store, "refresh_strategy", no_plan)
    # One country and two boards, so tier 1 is nine searches. The demo profile names five cities in
    # five countries, and its tier 1 alone would be 33 — past the cap of 30 before tier 2.
    with transaction() as session:
        store = Store(session)
        store.put_profile(
            store.get_profile().model_copy(
                update={"locations": ["London"], "countries": ["GB"], "sites": ["builtinlondon.uk", "gradcracker.com"]}
            )
        )
    return World()


class TestTheOrder:
    def test_plan_then_discover_then_harvest_then_score(self, world):
        world.answers({1: [posting(i) for i in range(4)]})
        world.run()
        firsts = list(dict.fromkeys(world.events))
        assert firsts == ["discover", "harvest", "score"]

    def test_the_harvester_refuses_anything_not_classified_a_posting(self, world):
        """The drift guard: a results page that reached the queue some other way is not read."""
        world.answers({1: []})
        world.queue().push([Target("https://www.gradcracker.com/search/technology-it/graduate-jobs?page=2", "gradcracker.com",
                                   "open", "q", 0, "Graduate jobs", "", None)])  # fmt: skip
        out = world.run(mode="more")
        assert out.rejected["not a posting"] == 1 and world.read == []


class TestEightToTwelve:
    def test_thirty_queued_twelve_read_eighteen_left_and_none_paid_for(self, world):
        world.answers({1: [posting(i) for i in range(30)]})
        out = world.run()
        assert out.surfaced == wera().surface.max == 12
        assert len(world.read) == 12, "postings past the twelfth were read"
        assert out.queued == 18 and world.queue().size() == 18
        assert out.spend.credits_used == 0 and out.short is None

    def test_short_of_eight_the_next_tier_is_sent(self, world):
        world.answers({1: [posting(i) for i in range(3)], 2: [posting(i) for i in range(10, 16)]})
        out = world.run()
        assert out.tiers == [1, 2]
        assert out.surfaced == 9 and out.short is None

    def test_an_empty_market_ends_below_eight_and_says_why(self, world):
        world.answers({1: [posting(0)]})
        out = world.run()
        assert out.surfaced == 1
        assert out.short and "every search in the plan has been sent" in out.short
        assert any(code == "FEWER_THAN_8" for code, _ in out.errors) and out.partial

    def test_the_serper_cap_ends_it_and_is_named(self, world, monkeypatch):
        world.answers({1: [posting(0)]})
        monkeypatch.setattr(search_pipeline, "SearchCaps", SearchCaps)
        out = search_pipeline.run({}, now=1_762_850_000_000, caps=SearchCaps(max_serper_queries=2))
        assert world.serper.sent == 2
        assert out.short and "limit of 2 searches" in out.short

    def test_find_more_reads_the_queue_then_sends_a_tier_not_yet_sent(self, world):
        world.answers({1: [posting(i) for i in range(30)], 2: [posting(200 + i) for i in range(6)]})
        queue = world.queue()
        queue.push([Target(posting(100 + i), "builtinlondon.uk", "board", "q", i, "Graduate", "", None) for i in range(5)])
        queue.mark_tier_sent(1)
        out = world.run(mode="more")
        assert world.read[:5] == [posting(100 + i) for i in range(5)]  # the queue first
        assert out.tiers == [2]  # then the next tier — tier 1 was sent today already
        assert out.surfaced == 11

    def test_find_more_with_enough_waiting_never_searches(self, world):
        world.answers({1: [posting(i) for i in range(30)]})
        queue = world.queue()
        queue.push([Target(posting(100 + i), "builtinlondon.uk", "board", "q", i, "Graduate", "", None) for i in range(20)])
        queue.mark_tier_sent(1)
        out = world.run(mode="more")
        assert out.surfaced == 12 and out.tiers == [] and world.serper.asked == []
        assert world.queue().size() == 8


class TestBeforeAnythingIsSpent:
    def test_redis_down_stops_the_search_and_nothing_is_asked(self, client, outbound, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
        get_settings.cache_clear()
        monkeypatch.setattr(Store, "refresh_strategy", no_plan)
        monkeypatch.setattr(search_pipeline, "Serper", lambda **_k: pytest.fail("Serper was asked"))
        out = search_pipeline.run({}, now=1_762_850_000_000, caps=SearchCaps())
        assert [code for code, _ in out.errors] == ["REDIS_DOWN"]


class TestTheRunRow:
    def test_a_board_search_records_what_it_surfaced_and_queued(self, world, monkeypatch):
        monkeypatch.setattr(intake, "sites_due", lambda *_a: False)  # SRCH-12 is its own test
        world.answers({1: [posting(i) for i in range(20)]})
        with transaction() as session:
            ids = Store(session).start_runs(["board_search"], scheduled=True)
        intake.run(["board_search"], ids)
        with transaction() as session:
            row = session.get(IntakeRunRow, ids["board_search"])
            assert (row.status, row.queued, row.queries) == ("succeeded", 8, world.serper.sent)
            assert row.new_jobs == 12


def test_the_stages_keep_to_their_own_modules():
    """The Strategist and Serper never read a posting or pay Firecrawl; the Harvester never
    searches. Imports, checked from the source, so a shortcut can't creep in unnoticed."""
    root = Path(__file__).parents[1] / "wera_agent"

    def imports(path: str) -> set[str]:
        tree = ast.parse((root / path).read_text())
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                found |= {f"{node.module or ''}.{a.name}".strip(".") for a in node.names} | {node.module or ""}
            elif isinstance(node, ast.Import):
                found |= {a.name for a in node.names}
        return found

    reading = ("firecrawl_search", "extract", "harvest")
    for path in ("nodes/strategist.py", "sources/serper.py", "sources/targets.py", "sources/postings.py"):
        assert not [m for m in imports(path) if any(r in m for r in reading)], path
    assert not [m for m in imports("sources/harvest.py") if "serper" in m or "strategist" in m]
