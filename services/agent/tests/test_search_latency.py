"""Search latency, 22 Nov (SPEC 3.30): embeddings kept across restarts, Arbeitnow matched cheaply
and once a day, spans that show where the time goes, and the `search-latency` eval.

Nothing here embeds for real or reaches the network: the embedder is a counting stand-in, Redis is
fakeredis, and the Arbeitnow fixture's closeness figures were made once with the real embedder on
the 22 Nov feed (`fixtures/feeds/arbeitnow_titles_22nov.json`).
"""

import json
from pathlib import Path

import fakeredis
import httpx
import numpy as np
import pytest
import respx

from tests.test_more_feeds import AT, NOW, arbeit, arbeit_page, keys, planned, profile_of, reviewed  # noqa: F401
from wera_agent import search_latency
from wera_agent.domain import filters
from wera_agent.nodes import prefilter, strategist
from wera_agent.sources.feeds import arbeitnow
from wera_agent.sources.provider_guard import Guard
from wera_agent.sources.targets import TargetQueue

FIXTURE = Path(__file__).parent / "fixtures" / "feeds" / "arbeitnow_titles_22nov.json"


class Counting:
    """An embedder that isn't the hash one (so the cache is used) and counts what it is asked."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        out = np.zeros((len(texts), prefilter.DIMENSIONS), dtype=np.float32)
        for row, text in enumerate(texts):
            out[row, hash(text) % prefilter.DIMENSIONS] = 1.0
            out[row, len(text) % prefilter.DIMENSIONS] += 0.5
        return out


@pytest.fixture
def l2(monkeypatch):
    """A byte-level fakeredis as the vectors' Redis, and an empty first level."""
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    monkeypatch.setattr(prefilter, "_vectors", {})
    monkeypatch.setattr(prefilter, "_redis_down_until", [0.0])
    monkeypatch.setattr(prefilter, "_redis", lambda: client)
    return client


def restart(monkeypatch) -> None:
    """A server restart: the process's dict is gone, Redis isn't."""
    monkeypatch.setattr(prefilter, "_vectors", {})


class TestEmbeddingsKeptAcrossRestarts:
    def test_hits_are_never_embedded_again_even_after_a_restart(self, l2, monkeypatch):
        model = Counting()
        first = prefilter.embed_cached(["Financial Analyst", "Equity Analyst"], model)
        assert model.calls == [["Financial Analyst", "Equity Analyst"]]
        restart(monkeypatch)
        again = prefilter.embed_cached(["Equity Analyst", "Financial Analyst"], model)
        assert len(model.calls) == 1  # from Redis
        assert np.allclose(again, first[::-1])
        prefilter.embed_cached(["Financial Analyst"], model)
        assert len(model.calls) == 1  # from memory

    def test_misses_go_in_one_batch_and_rows_keep_their_order(self, l2):
        model = Counting()
        prefilter.embed_cached(["a"], model)
        got = prefilter.embed_cached(["c", "a", "d", "c"], model)
        assert model.calls[1] == ["c", "d"]  # one batch, each text once
        assert np.allclose(got, Counting().embed(["c", "a", "d", "c"]))

    def test_kept_as_float32_bytes_for_14_days_under_the_models_key(self, l2):
        prefilter.embed_cached(["Analyst"], Counting())
        [key] = l2.keys("wera:emb:*")
        assert key.decode().startswith(f"wera:emb:{prefilter.MODEL_NAME}:")
        assert len(l2.get(key)) == prefilter.DIMENSIONS * 4
        assert 14 * 86_400 - 10 < l2.ttl(key) <= 14 * 86_400

    def test_a_bad_value_is_a_miss_and_is_replaced(self, l2, monkeypatch):
        model = Counting()
        prefilter.embed_cached(["Analyst"], model)
        [key] = l2.keys("wera:emb:*")
        l2.set(key, b"not a vector")
        restart(monkeypatch)
        got = prefilter.embed_cached(["Analyst"], model)
        assert len(model.calls) == 2 and np.allclose(got, Counting().embed(["Analyst"]))
        assert len(l2.get(key)) == prefilter.DIMENSIONS * 4

    def test_redis_down_falls_back_to_embedding(self, monkeypatch):
        """The suite's REDIS_URL is a port nothing serves: the real client fails, the text is
        embedded, and Redis is left alone for a minute rather than asked once a call."""
        monkeypatch.setattr(prefilter, "_vectors", {})
        monkeypatch.setattr(prefilter, "_redis_clients", {})
        monkeypatch.setattr(prefilter, "_redis_down_until", [0.0])
        model = Counting()
        got = prefilter.embed_cached(["Analyst", "Trader"], model)
        assert model.calls == [["Analyst", "Trader"]] and got.shape == (2, prefilter.DIMENSIONS)
        assert prefilter._redis() is None

    def test_the_hash_embedder_never_touches_redis(self, monkeypatch):
        def refuse():
            raise AssertionError("the demo embedder asked Redis")

        monkeypatch.setattr(prefilter, "_redis", refuse)
        got = prefilter.embed_cached(["Data Analyst"], prefilter.HashEmbedder())
        assert np.allclose(got, prefilter.HashEmbedder().embed(["Data Analyst"]))

    def test_the_ranker_the_titles_and_the_shortlist_all_use_it(self, l2, monkeypatch, demo_cv):
        model = Counting()
        monkeypatch.setitem(prefilter._cached, "fast", model)
        rank = prefilter.lead_ranker(demo_cv, ["Analyst"], demo_mode=False)
        rank([("Analyst", "numbers")])
        restart(monkeypatch)
        rank = prefilter.lead_ranker(demo_cv, ["Analyst"], demo_mode=False)
        rank([("Analyst", "numbers")])
        assert len(model.calls) == 2  # the CV and the lead, once each

        monkeypatch.setattr(prefilter, "_title_model", lambda: model)
        monkeypatch.setattr(prefilter, "_titles", {})
        prefilter.warm_titles(["Quant Trader"])
        monkeypatch.setattr(prefilter, "_titles", {})
        restart(monkeypatch)
        prefilter.warm_titles(["Quant Trader"])
        assert model.calls[-1] == ["Quant Trader"] and len(model.calls) == 3


# -- Arbeitnow --------------------------------------------------------------------------------------


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def kept_by_the_full_match(titles: list[str], targets: list[str], close: dict[str, float]) -> list[str]:
    return [t for t in titles if filters.matches_a_target(t, targets) or close.get(t, 0.0) >= prefilter.TITLE_MEANING_MIN]


class TestArbeitnowMatchedCheaply:
    @pytest.mark.parametrize("career", sorted(json.loads(FIXTURE.read_text(encoding="utf-8"))["profiles"]))
    def test_the_word_pre_filter_keeps_every_title_the_full_match_keeps(self, career):
        """On the 22 Nov feed's 2,907 distinct titles, by the real embedder's closeness, for 13
        careers' titles (finance, software, data, marketing, sales, health, design, accounting,
        HR, operations, engineering, legal, teaching and support)."""
        data = fixture()
        titles, profile = data["titles"], data["profiles"][career]
        near = arbeitnow.Near.of(profile["targets"])
        kept = kept_by_the_full_match(titles, profile["targets"], profile["close"])
        assert kept and [t for t in kept if not near(t)] == []
        # And it is a pre-filter: well under half the titles go on to be embedded.
        assert sum(map(near, titles)) < 0.4 * len(titles)

    def test_level_words_and_stems_are_what_keep_the_close_calls(self):
        near = arbeitnow.Near.of(["Junior Financial Modeller", "Analyst"])
        assert near("Analytics Engineer") and near("Account Manager Junior")
        assert not near("Koch (m/w/d)")

    @respx.mock
    def test_a_read_matches_the_same_jobs_as_the_full_match_and_asks_only_the_near_ones(self, outbound, monkeypatch):
        data = fixture()
        profile = data["profiles"]["finance"]
        rows = [arbeit(t, location="London", n=i) for i, t in enumerate(data["titles"])]
        respx.get(arbeitnow.FEED).mock(return_value=httpx.Response(200, json=arbeit_page([], page=1, last=True)))
        respx.get(arbeitnow.FEED_UK).mock(return_value=httpx.Response(200, json=arbeit_page(rows, page=1, last=True)))
        asked: list[str] = []

        def by_fixture(title: str, targets: list[str]) -> bool:
            asked.append(title)
            return profile["close"].get(title, 0.0) >= prefilter.TITLE_MEANING_MIN

        warmed: list[str] = []
        monkeypatch.setattr(filters, "close_in_meaning", by_fixture)
        monkeypatch.setattr(prefilter, "warm_titles", lambda titles: warmed.extend(titles))
        got = arbeitnow.read(
            Guard(None, "arbeitnow"),
            targets=profile["targets"],
            countries=["gb"],
            remote_ok=True,
            max_pages=1,
            max_leads=10_000,
            now=AT,
        )
        expected = list(dict.fromkeys(kept_by_the_full_match(data["titles"], profile["targets"], profile["close"])))
        assert [lead.title for lead in got.leads] == expected
        near = arbeitnow.Near.of(profile["targets"])
        assert asked and all(near(t) for t in asked)
        assert len(set(warmed)) < 0.4 * len(data["titles"])  # what is embedded, against 2,907

    @respx.mock
    def test_the_days_second_search_matches_nothing(self, outbound, monkeypatch):
        redis = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        rows = [arbeit("Data Analyst (m/w/d)", n=1), arbeit("Koch", n=2), arbeit("Analytics Specialist", n=3)]
        route = respx.get(arbeitnow.FEED).mock(return_value=httpx.Response(200, json=arbeit_page(rows, page=1, last=True)))
        meant: list[str] = []
        monkeypatch.setattr(
            filters, "close_in_meaning", lambda title, targets: meant.append(title) or title == "Analytics Specialist"
        )
        kw = {"targets": ["Data Analyst"], "countries": ["de"], "remote_ok": True, "max_pages": 1, "max_leads": 40, "now": AT}
        first = arbeitnow.read(Guard(redis, "arbeitnow"), **kw)
        assert [lead.title for lead in first.leads] == ["Data Analyst (m/w/d)", "Analytics Specialist"] and meant
        meant.clear()
        before = dict(arbeitnow.STATS)
        again = arbeitnow.read(Guard(redis, "arbeitnow"), **kw)
        assert (again.how, again.read, meant, route.call_count) == ("cached", 3, [], 1)
        assert again.leads == first.leads
        assert arbeitnow.STATS["kept"] == before.get("kept", 0) + 1
        [key] = redis.keys(f"{arbeitnow.MATCHED}*")
        assert 86_000 < redis.ttl(key) <= 86_400
        # Another day, other titles or other countries are another question.
        arbeitnow.read(Guard(redis, "arbeitnow"), **(kw | {"countries": ["de", "at"]}))
        assert meant


# -- Spans ------------------------------------------------------------------------------------------


class Spans:
    def __init__(self) -> None:
        self.names: list[str] = []

    def __call__(self, template: str, **attrs):
        self.names.append(template.format_map(_Blank(attrs)))
        return _Span()


class _Blank(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class _Span:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set_attributes(self, _attrs):
        pass

    def set_attribute(self, *_a):
        pass


@pytest.fixture
def spans(monkeypatch):
    import logfire

    recorder = Spans()
    monkeypatch.setattr(logfire, "span", recorder)
    return recorder


class TestSpansShowWhereTimeGoes:
    def test_an_embedding_batch_says_how_many_and_how_many_were_cached(self, l2, spans):
        prefilter.embed_cached(["a", "b"], Counting())
        prefilter.embed_cached(["a", "c"], Counting())
        assert spans.names[-2:] == ["embed n=2 cached=0", "embed n=2 cached=1"]

    @respx.mock(assert_all_called=False)
    def test_each_feed_call_has_its_own_span(self, keys, reviewed, store, respx_mock, spans):  # noqa: F811
        redis = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        respx_mock.get(arbeitnow.FEED).mock(return_value=httpx.Response(200, json=arbeit_page([arbeit("Data Analyst", n=1)], page=1, last=True)))  # fmt: skip
        respx_mock.get(arbeitnow.FEED_UK).mock(return_value=httpx.Response(200, json=arbeit_page([], page=1, last=True)))
        strategist.discover_feeds(
            planned("Graduate Data Analyst", "gb", "de"),
            profile_of(store),
            queue=TargetQueue(redis, "plan"),
            known_urls=set(),
            sites=[],
            now=NOW,
            redis=redis,
        )
        assert "feed arbeitnow" in spans.names

    def test_a_scoring_batch_has_its_span(self, client, spans):
        from wera_agent.graphs import intake

        intake.judge([], now=NOW, run_ids={})
        assert any(name.startswith("score batch n=") for name in spans.names)


# -- The eval ---------------------------------------------------------------------------------------


class TestTheSearchLatencyEval:
    def fake_search(self, log: list[dict], *, embed: int = 0, sleep_judge: float = 0.0):
        def search() -> str:
            from wera_agent.db.models import IntakeRunRow
            from wera_agent.db.session import transaction
            from wera_agent.graphs import intake
            from wera_agent.services.store import Store

            with transaction() as session:
                ids = Store(session).start_runs(["board_search"], scheduled=False)
            with transaction() as session:
                row = session.get(IntakeRunRow, ids["board_search"])
                row.log, row.status, row.new_jobs = log, "succeeded", 1
            if embed:
                prefilter.embed_cached([f"t{i}" for i in range(embed)], Counting())
                prefilter.embed_cached([f"t{i}" for i in range(embed)], Counting())
            intake.judge([], now=NOW, run_ids={})  # timed as "scoring"
            return ids["board_search"]

        return search

    def test_steps_caches_and_a_saved_baseline(self, client, l2, tmp_path):
        log = [{"phase": "planning", "ms": 2000}, {"phase": "apis", "ms": 30_000}, {"phase": "apis", "ms": 5000}]
        base = tmp_path / "baseline.json"
        report = search_latency.run(baseline_path=base, search=self.fake_search(log, embed=3), check_env=False)
        assert report.steps["planning"] == 2.0 and report.steps["apis"] == 35.0
        assert {"scoring", "embedding", "total"} <= set(report.steps)
        assert (report.caches["embeddings"]["texts"], report.caches["embeddings"]["embedded"]) == (6, 3)
        assert report.caches["embeddings"]["hit_rate"] == 0.5
        assert report.status == "succeeded" and report.new_jobs == 1
        assert base.exists() and report.slower == []  # the first run is the baseline
        assert "| apis | 35.0 |" in search_latency.as_markdown(report)

    def test_a_step_more_than_half_as_slow_again_is_flagged(self, client, tmp_path):
        base = tmp_path / "baseline.json"
        search_latency.run(baseline_path=base, search=self.fake_search([{"phase": "apis", "ms": 20_000}]), check_env=False)
        slow = search_latency.run(baseline_path=base, search=self.fake_search([{"phase": "apis", "ms": 31_000}]), check_env=False)
        assert [f["step"] for f in slow.slower] == ["apis"] and slow.baseline["apis"] == 20.0
        assert "**slower**" in search_latency.as_markdown(slow)
        fine = search_latency.run(baseline_path=base, search=self.fake_search([{"phase": "apis", "ms": 29_000}]), check_env=False)
        assert [f["step"] for f in fine.slower] == []

    def test_noise_on_a_short_step_is_not_a_regression(self):
        assert search_latency.compare({"planning": 0.9}, {"planning": 0.3}) == []
        assert search_latency.compare({"planning": 4.0}, {"planning": 2.0})[0]["ratio"] == 2.0

    def test_it_refuses_the_live_database_and_redis_db_0(self, monkeypatch):
        from wera_agent.settings import get_settings

        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/wera")
        get_settings.cache_clear()
        with pytest.raises(SystemExit, match="wera_eval"):
            search_latency.measure(search=lambda: "x")
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost:5432/wera_eval")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        get_settings.cache_clear()
        with pytest.raises(SystemExit, match="db 0"):
            search_latency.measure(search=lambda: "x")
        get_settings.cache_clear()
