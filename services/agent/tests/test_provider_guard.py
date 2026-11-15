"""SRCH-33: shared answers, one caller at a time, a breaker and a day's meter, in Redis.

What matters is what each costs: an answer any search already paid for is free and uncounted, a
query being asked right now is asked once however many wait for it, and a provider that is
failing is left alone by everyone — while a broken Redis changes nothing but the bill.
"""

import threading
import time

import fakeredis
import httpx
import pytest
import respx

from wera_agent.config import BreakerConfig, GuardConfig
from wera_agent.settings import get_settings
from wera_agent.sources import serper, targets
from wera_agent.sources.provider_guard import Guard, answer_key

ORGANIC = {"organic": [{"title": "Graduate Software Engineer", "link": "https://builtinlondon.uk/job/grad-swe/1", "snippet": "London", "position": 1}]}  # fmt: skip
CONFIG = GuardConfig(cache_minutes=180, lock_seconds=2, breaker=BreakerConfig(failures=3, window_seconds=60, open_seconds=60))


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture
def key(outbound, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def guard(redis, **over) -> Guard:
    return Guard(redis, "serper", config=over.pop("config", CONFIG), sleep=over.pop("sleep", lambda _s: None), **over)


def client(redis, **over) -> serper.Serper:
    g = over.pop("guard", None) or guard(redis, config=over.pop("config", CONFIG))
    return serper.Serper(**({"max_queries": 30, "sleep": lambda _s: None, "guard": g} | over))


class TestTheGuard:
    def test_an_answer_is_shared_for_cache_minutes(self, redis):
        g = guard(redis)
        g.remember("k", [{"a": 1}])
        assert Guard(redis, "serper", config=CONFIG).answer("k") == [{"a": 1}]
        assert 0 < redis.ttl("wera:answer:k") <= 180 * 60

    def test_one_caller_leads_and_only_it_can_let_go(self, redis):
        first, second = guard(redis), guard(redis)
        token = first.lead("k")
        assert token and second.lead("k") is None
        second.release("k", "not-the-token")
        assert redis.exists("wera:lock:k")
        first.release("k", token)
        assert not redis.exists("wera:lock:k")

    def test_a_waiter_gets_the_answer_when_it_lands(self, redis):
        leader = guard(redis)
        leader.lead("k")
        waiter = guard(redis, sleep=lambda _s: leader.remember("k", ["found"]))
        assert waiter.wait_for("k") == ["found"]

    def test_a_waiter_stops_waiting_when_the_leader_lets_go_without_an_answer(self, redis):
        leader = guard(redis)
        token = leader.lead("k")
        waiter = guard(redis, sleep=lambda _s: leader.release("k", token))
        assert waiter.wait_for("k") is None

    def test_a_waiter_gives_up_after_lock_seconds(self, redis):
        guard(redis).lead("k")
        now = [0.0]

        def tick(seconds):
            now[0] += seconds

        assert guard(redis, sleep=tick, clock=lambda: now[0]).wait_for("k") is None
        assert now[0] == pytest.approx(CONFIG.lock_seconds, abs=0.2)

    def test_failures_inside_the_window_open_the_breaker(self, redis):
        g = guard(redis)
        assert not g.failed("HTTP 503") and not g.failed("HTTP 503")
        assert g.failed("HTTP 503")
        assert 0 < g.open_for() <= 60 and "3 failures" in g.why_open()
        g.reset()
        assert g.open_for() == 0

    def test_the_day_is_metered_and_a_search_stops_at_its_share(self, redis):
        g = guard(redis, config=CONFIG.model_copy(update={"daily": {"serper": 10}}), today=lambda: "2026-11-13")
        g.count(7)
        assert g.used_today() == 7 and not g.spent()
        g.count()
        assert g.spent()  # 8 of 10 is the 80% searches may use
        assert guard(redis, today=lambda: "2026-11-14").used_today() == 0

    def test_clear_jobs_clears_answers_but_not_breakers_or_meters(self, redis):
        g = guard(redis)
        g.remember("k", [])
        g.count()
        g.trip("HTTP 429")
        targets.clear_all(redis)
        assert g.answer("k") is None
        assert g.used_today() == 1 and g.open_for() > 0

    def test_without_redis_the_guard_lets_everything_through(self):
        g = guard(None)
        assert g.lead("k") and g.answer("k") is None and g.wait_for("k") is None
        assert g.open_for() == 0 and not g.spent() and not g.failed("x")

    def test_a_broken_redis_is_a_guard_that_does_nothing(self):
        class Broken:
            def __getattr__(self, _name):
                def fail(*_a, **_k):
                    raise ConnectionError("Redis went away")

                return fail

        g = guard(Broken())
        g.remember("k", [1])
        assert g.lead("k") and g.answer("k") is None and g.open_for() == 0 and not g.spent()


class TestSerperBehindTheGuard:
    @respx.mock
    def test_a_query_any_search_asked_is_answered_free(self, key, redis):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        assert client(redis).search("q", gl="gb", hl="en")
        later = client(redis)  # another search, another person: the same question
        assert [r.url for r in later.search("q", gl="gb", hl="en")] == ["https://builtinlondon.uk/job/grad-swe/1"]
        assert (route.call_count, later.sent, later.cached, later.credits_used) == (1, 0, 1, 0)
        client(redis).search("q", gl="us", hl="en")  # a different market is a different question
        assert route.call_count == 2

    @respx.mock
    def test_a_free_answer_is_not_counted_against_the_cap(self, key, redis):
        respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        client(redis).search("a", gl="gb", hl="en")
        c = client(redis, max_queries=1)
        assert c.search("a", gl="gb", hl="en") and c.search("b", gl="gb", hl="en")
        assert c.search("c", gl="gb", hl="en") is None and c.stopped == "cap"

    @respx.mock
    def test_a_query_being_asked_is_waited_for_not_asked_again(self, key, redis):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        body = {"q": "q", "gl": "gb", "hl": "en", "num": serper.RESULTS_PER_QUERY, "tbs": serper.WINDOW}
        held = answer_key("serper", body)
        other = guard(redis)
        other.lead(held)  # another search is asking it right now
        c = client(redis, guard=guard(redis, sleep=lambda _s: other.remember(held, [ORGANIC_ROW])))
        assert [r.title for r in c.search("q", gl="gb", hl="en")] == ["Graduate Software Engineer"]
        assert (route.call_count, c.coalesced, c.sent) == (0, 1, 0)

    @respx.mock
    def test_five_searches_at_once_send_one_query(self, key, redis):
        def slow(_request):
            time.sleep(0.3)
            return httpx.Response(200, json=ORGANIC)

        route = respx.post(serper.ENDPOINT).mock(side_effect=slow)
        clients = [serper.Serper(max_queries=30, guard=Guard(redis, "serper", config=CONFIG)) for _ in range(5)]
        answers: list = []
        threads = [threading.Thread(target=lambda c=c: answers.append(c.search("q", gl="gb", hl="en"))) for c in clients]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert route.call_count == 1
        assert all(a and a[0].url == "https://builtinlondon.uk/job/grad-swe/1" for a in answers)
        assert sum(c.sent for c in clients) == 1 and sum(c.coalesced for c in clients) == 4

    @respx.mock
    def test_a_rate_limit_that_outlasts_the_retries_stops_every_search(self, key, redis):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(429, headers={"retry-after": "30"}))
        first = client(redis)
        assert first.search("a", gl="gb", hl="en") is None and first.stopped == "breaker"
        assert 0 < redis.ttl("wera-meter:breaker:serper") <= 30
        second = client(redis)
        assert second.search("b", gl="gb", hl="en") is None and second.stopped == "breaker"
        assert route.call_count == serper.MAX_TRIES  # the second search never asked

    @respx.mock
    def test_three_failed_queries_open_the_breaker(self, key, redis):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(503))
        c = client(redis)
        for q in ("a", "b", "c"):
            c.search(q, gl="gb", hl="en")
        assert c.stopped == "breaker"
        assert c.search("d", gl="gb", hl="en") is None and route.call_count == 3 * serper.MAX_TRIES

    @respx.mock
    def test_a_spent_day_stops_the_search_before_it_asks(self, key, redis):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        g = guard(redis, config=CONFIG.model_copy(update={"daily": {"serper": 10}}))
        g.count(7)
        c = client(redis, guard=g)
        assert c.search("a", gl="gb", hl="en")  # the eighth
        assert c.search("b", gl="gb", hl="en") is None and c.stopped == "daily_budget"
        assert route.call_count == 1 and g.used_today() == 8

    @respx.mock
    def test_without_redis_serper_is_asked_as_before(self, key):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        c = client(None, guard=guard(None))
        assert c.search("q", gl="gb", hl="en") and c.search("q", gl="gb", hl="en")
        assert route.call_count == 2 and c.cached == 0


ORGANIC_ROW = {"url": "https://builtinlondon.uk/job/grad-swe/1", "title": "Graduate Software Engineer", "snippet": "London", "date": None, "position": 1}  # fmt: skip
