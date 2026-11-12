"""SRCH-20, stage 2: asking Google through Serper.

The client has three jobs worth testing: send the query in the shape Serper wants, spend no more
than the cap allows, and give up on the right failures — retrying a rate limit, stopping on a
refused key — so a run neither hammers a provider nor pays to be told no twice.
"""

import json

import httpx
import pytest
import respx

from wera_agent.errors import ApiError
from wera_agent.settings import get_settings
from wera_agent.sources import serper

ORGANIC = {
    "organic": [
        {"title": "Graduate Software Engineer", "link": "https://builtinlondon.uk/job/grad-swe/12138050", "snippet": "London",
         "date": "3 days ago", "position": 1},
        {"title": "On LinkedIn", "link": "https://www.linkedin.com/jobs/view/123", "snippet": "x", "position": 2},
        {"title": "No link", "position": 3},
    ]
}  # fmt: skip


@pytest.fixture
def key(outbound, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def client(**over) -> serper.Serper:
    return serper.Serper(**({"max_queries": 30, "sleep": lambda _s: None} | over))


class TestTheRequest:
    @respx.mock
    def test_the_shape_serper_wants(self, key):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        client().search('"Graduate Software Engineer" London -site:linkedin.com', gl="gb", hl="en")
        sent = route.calls.last.request
        assert sent.headers["X-API-KEY"] == "test-serper-key"
        assert json.loads(sent.content) == {"q": '"Graduate Software Engineer" London -site:linkedin.com', "gl": "gb", "hl": "en", "num": 10,
                        "tbs": "qdr:w3"}  # fmt: skip

    @respx.mock
    def test_a_second_page_is_asked_for_by_number(self, key):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        client().search("q", gl="gb", hl="en", page=2)
        assert json.loads(route.calls.last.request.content)["page"] == 2

    @respx.mock
    def test_results_come_back_without_linkedin_or_junk(self, key):
        respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        results = client().search("q", gl="gb", hl="en")
        assert [(r.url, r.date, r.position) for r in results] == [
            ("https://builtinlondon.uk/job/grad-swe/12138050", "3 days ago", 1)
        ]

    def test_no_key_no_search(self, outbound):
        with pytest.raises(ApiError) as exc:
            client()
        assert exc.value.code == "SERPER_KEY_MISSING"

    def test_demo_mode_makes_no_request(self, monkeypatch):
        monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
        get_settings.cache_clear()
        with pytest.raises(ApiError) as exc:
            client().search("q", gl="gb", hl="en")
        assert exc.value.code == "DEMO_MODE"


class TestWhatItSpends:
    @respx.mock
    def test_the_cap_is_a_ceiling(self, key):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(200, json=ORGANIC))
        c = client(max_queries=2)
        assert c.search("a", gl="gb", hl="en") is not None
        assert c.search("b", gl="gb", hl="en") is not None
        assert c.search("c", gl="gb", hl="en") is None
        assert (route.call_count, c.sent, c.stopped) == (2, 2, "cap")

    @respx.mock
    def test_a_rate_limit_is_retried(self, key):
        route = respx.post(serper.ENDPOINT).mock(side_effect=[httpx.Response(429), httpx.Response(200, json=ORGANIC)])
        c = client()
        assert c.search("q", gl="gb", hl="en")
        assert (route.call_count, c.sent, c.credits_used) == (2, 1, 1)

    @respx.mock
    def test_a_server_error_is_given_up_on_after_three_tries(self, key):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(503))
        c = client()
        assert c.search("q", gl="gb", hl="en") is None
        assert route.call_count == serper.MAX_TRIES and c.credits_used == 0 and c.errors

    @respx.mock
    def test_a_refused_key_stops_the_run(self, key):
        route = respx.post(serper.ENDPOINT).mock(return_value=httpx.Response(401))
        c = client()
        assert c.search("a", gl="gb", hl="en") is None
        assert c.search("b", gl="gb", hl="en") is None  # not asked again
        assert (route.call_count, c.stopped) == (1, "SERPER_AUTH")

    @respx.mock
    def test_a_window_serper_refuses_falls_back_to_the_month(self, key):
        route = respx.post(serper.ENDPOINT).mock(side_effect=[httpx.Response(400), httpx.Response(200, json=ORGANIC)])
        c = client()
        assert c.search("q", gl="gb", hl="en")
        assert json.loads(route.calls.last.request.content)["tbs"] == serper.FALLBACK_WINDOW
        assert c.window == serper.FALLBACK_WINDOW  # kept for the rest of the run
