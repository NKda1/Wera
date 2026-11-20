"""SRCH-31/32: the job APIs, asked before Serper.

Recorded answers (13 Nov) from Reed, USAJOBS and Adzuna; Careerjet's is its documented shape,
since its key only works from the publisher account's registered IPs. What matters: each is
asked the way it searches best, never with CV text; nothing is called before its terms are
reviewed; a full-text API's postings are read from the API; and a snippet API's tracked link is
never requested — its lead's original posting is found with Serper instead.
"""

import json
from datetime import date
from pathlib import Path

import fakeredis
import httpx
import pytest
import respx

from wera_agent.clock import parse_ms
from wera_agent.config import FeedConfig, Site, wera
from wera_agent.nodes import strategist
from wera_agent.schemas.strategy import PlannedQuery
from wera_agent.settings import get_settings
from wera_agent.sources import feeds, harvest
from wera_agent.sources.feeds import adzuna, careerjet, reed, resource, usajobs
from wera_agent.sources.firecrawl_search import SearchCaps, SearchOutcome
from wera_agent.sources.provider_guard import Guard
from wera_agent.sources.serper import Result
from wera_agent.sources.targets import TargetQueue

FIXTURES = Path(__file__).parent / "fixtures" / "feeds"
NOW = parse_ms("2026-11-13T12:00:00Z")
UK = feeds.FeedQuery("Graduate Software Engineer", "Software Engineer", "graduate", "London", "gb")
US = feeds.FeedQuery("Graduate Software Engineer", "Software Engineer", "graduate", None, "us")
PERSON = feeds.Person(ip="203.0.113.7", user_agent="Mozilla/5.0 (test)")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def keys(outbound, monkeypatch):
    for name, value in {
        "REED_API_KEY": "test-reed",
        "USAJOBS_API_KEY": "test-usajobs",
        "USAJOBS_EMAIL": "person@example.com",
        "ADZUNA_APP_ID": "test-app",
        "ADZUNA_APP_KEY": "test-adzuna",
        "CAREERJET_API_KEY": "test-careerjet",
        "SERPER_API_KEY": "test-serper",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def reviewed(monkeypatch):
    """Every API's terms reviewed, as config/wera.yaml would say once they are."""
    for feed_id in feeds.FEEDS:
        monkeypatch.setitem(wera().feeds, feed_id, FeedConfig(terms_reviewed_on=date(2026, 11, 13), max_searches=1, max_queued=2))


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


class TestTheQueries:
    def test_a_level_word_is_kept_apart_from_the_role(self):
        assert feeds.split_level("Graduate Software Engineer") == ("Software Engineer", "graduate")
        assert feeds.split_level("Software Engineering Internship") == ("Software Engineering", "intern")
        assert feeds.split_level("Backend Engineer") == ("Backend Engineer", None)

    def test_one_query_per_title_and_place(self):
        qs = feeds.queries_for(["Graduate Software Engineer", "Backend Engineer"], [("London", "gb")])
        assert [(q.role, q.level, q.city, q.country) for q in qs] == [
            ("Software Engineer", "graduate", "London", "gb"),
            ("Backend Engineer", None, "London", "gb"),
        ]

    def test_a_city_goes_with_its_own_country(self):
        """16 Nov: London and Switzerland asked Reed for the whole UK, because with two countries
        the city was dropped. Each city now goes with the country it is in."""
        from wera_agent.domain import places

        wanted = places.where(["CH", "GB"], ["London, UK"])
        only = feeds.places_for(wanted, city_scope="only", countries=["ch", "gb"])
        assert only == [(None, "ch"), ("London", "gb")]
        # `first`: the rest of the country after its city.
        first = feeds.places_for(wanted, city_scope="first", countries=["ch", "gb"])
        assert first == [(None, "ch"), ("London", "gb"), (None, "gb")]


class TestTheGate:
    def test_nothing_is_called_before_its_terms_are_reviewed(self, keys, monkeypatch):
        monkeypatch.setitem(wera().feeds, "reed", FeedConfig(terms_reviewed_on=None))
        assert "terms" in feeds.skipped("reed", countries={"gb"}, person=None)

    def test_an_api_runs_only_where_it_has_jobs(self, keys, reviewed):
        assert feeds.skipped("reed", countries={"gb"}, person=None) is None
        assert "no jobs in US" in feeds.skipped("reed", countries={"us"}, person=None)
        assert feeds.skipped("usajobs", countries={"us"}, person=None) is None

    def test_a_missing_key_is_named(self, keys, reviewed, monkeypatch):
        monkeypatch.setenv("USAJOBS_EMAIL", "")
        get_settings.cache_clear()
        assert feeds.skipped("usajobs", countries={"us"}, person=None) == "USAJOBS_EMAIL not set in .env.local"

    def test_careerjet_needs_the_person_searching(self, keys, reviewed):
        assert "person" in feeds.skipped("careerjet", countries={"gb"}, person=None)
        assert feeds.skipped("careerjet", countries={"gb"}, person=PERSON) is None


class TestReed:
    @respx.mock
    def test_a_search_asks_for_the_role_at_graduate_level_near_the_city(self, keys):
        route = respx.get(reed.SEARCH).mock(return_value=httpx.Response(200, json=fixture("reed_search")))
        leads, how = reed.search(UK, Guard(None, "reed"), graduate=True)
        sent = route.calls.last.request
        assert dict(sent.url.params) == {"keywords": "Software Engineer", "resultsToTake": "100", "locationName": "London", "distanceFromLocation": "15", "graduate": "true"}  # fmt: skip
        assert sent.headers["authorization"].startswith("Basic ")  # the key as the username
        assert how == "fetched" and [(lead.title, lead.date, lead.payload) for lead in leads][:2] == [
            ("Software Engineer", "2026-11-09", {"jobId": 57544665}),
            ("Associate Backend Software Engineer", "2026-10-14", {"jobId": 57457146}),
        ]

    @respx.mock
    def test_a_posting_is_read_whole_from_the_details_call(self, keys):
        respx.get(reed.DETAILS.format(id=57544665)).mock(return_value=httpx.Response(200, json=fixture("reed_job")))
        card = reed.read({"jobId": 57544665}, Guard(None, "reed"))
        assert (card["title"], card["company"], card["postedAt"]) == ("Software Engineer", "Wissen Technology", "2026-11-09")
        assert len(card["description"]) > 2000 and "<p>" not in card["description"]
        assert card["salary"] == {"min": 40000.0, "max": 55000.0, "currency": "GBP", "period": "year"}

    @respx.mock
    def test_an_unknown_id_is_a_blank_job_not_a_posting(self, keys):
        respx.get(reed.DETAILS.format(id=1)).mock(return_value=httpx.Response(200, json={"jobId": 0}))
        assert reed.read({"jobId": 1}, Guard(None, "reed")) is None


class TestUsajobs:
    @respx.mock
    def test_the_headers_usajobs_requires_and_the_full_posting_in_the_answer(self, keys):
        route = respx.get(usajobs.SEARCH).mock(return_value=httpx.Response(200, json=fixture("usajobs_search")))
        leads, _how = usajobs.search(US, Guard(None, "usajobs"), pathways=True)
        sent = route.calls.last.request
        assert sent.headers["user-agent"] == "person@example.com" and sent.headers["authorization-key"] == "test-usajobs"
        assert sent.url.params["Fields"] == "Full" and sent.url.params["DatePosted"] == "21"
        assert sent.url.params["HiringPath"] == "graduates;student"
        card = leads[0].payload["card"]
        assert (card["title"], card["postedAt"]) == ("General Engineer", "2026-11-13")
        assert card["description"].startswith("## Summary") and "## Duties" in card["description"]
        assert card["salary"]["currency"] == "USD" and card["salary"]["period"] == "year"


class TestTheSnippetApis:
    @respx.mock
    def test_adzuna_is_asked_for_the_level_in_the_title_and_the_role_anywhere(self, keys):
        route = respx.get(adzuna.SEARCH.format(country="gb")).mock(
            return_value=httpx.Response(200, json=fixture("adzuna_search"))
        )
        leads, _how = adzuna.search(UK, Guard(None, "adzuna"))
        params = route.calls.last.request.url.params
        assert (params["title_only"], params["what"], params["where"], params["max_days_old"]) == (
            "graduate",
            "Software Engineer",
            "London",
            "21",
        )
        assert leads[0].company == "Arch Capital Group" and leads[0].url.startswith("https://www.adzuna.co.uk/jobs/land/")

    @respx.mock
    def test_careerjet_gets_the_person_but_shares_the_answer_without_them(self, keys, redis):
        route = respx.get(careerjet.SEARCH).mock(return_value=httpx.Response(200, json=fixture("careerjet_search")))
        leads, _ = careerjet.search(UK, Guard(redis, "careerjet"), person=PERSON)
        params = route.calls.last.request.url.params
        assert (params["user_ip"], params["user_agent"], params["locale_code"]) == ("203.0.113.7", "Mozilla/5.0 (test)", "en_GB")
        assert route.calls.last.request.headers["referer"] == "http://localhost:3000"  # "Undeclared referrer" without
        assert [(lead.title, lead.date) for lead in leads] == [
            ("Graduate Software Engineer", "2026-11-10"),
            ("Senior Software Engineer", "2026-11-09"),
        ]
        _, how = careerjet.search(UK, Guard(redis, "careerjet"), person=feeds.Person(ip="198.51.100.1", user_agent="other"))
        assert how == "cached" and route.call_count == 1


class FakeSerper:
    def __init__(self, answers: dict[str, list[Result]]):
        self.answers, self.sent, self.cached, self.errors, self.asked = answers, 0, 0, [], []

    def search(self, q, *, gl, hl, page=1):
        self.asked.append(q)
        self.sent += 1
        return next((rs for key, rs in self.answers.items() if key in q), [])


class TestFindingTheOriginal:
    LEAD = feeds.Lead(url="https://www.adzuna.co.uk/jobs/land/ad/1", title="2027 Technology Graduate Programme", company="TD Bank", location="London", date="2026-10-23", snippet="")  # fmt: skip

    def test_one_search_for_the_title_and_company_finds_the_employers_posting(self):
        serper = FakeSerper({'"TD Bank"': [
            Result("https://www.adzuna.co.uk/details/1", "2027 Technology Graduate Programme", "", None, 1),
            Result("https://td.wd3.myworkdayjobs.com/en-US/TD_Bank_Careers/job/London/2027-Technology-Graduate-Programme_R123", "2027 Technology Graduate Programme - TD", "TD Bank London", None, 2),
        ]})  # fmt: skip
        hit = resource.original(self.LEAD, serper=serper, sites=[], gl="gb", hl="en", exclude=["indeed.com"])
        assert hit is not None and "myworkdayjobs.com" in hit.url
        assert serper.asked == ['"2027 Technology Graduate Programme" "TD Bank" -site:adzuna.co.uk -site:careerjet.co.uk -site:jobviewtrack.com -site:bebee.com -site:jobleads.com -site:indeed.com']  # fmt: skip

    def test_a_senior_result_isnt_the_original_of_a_lead_without_one(self):
        lead = feeds.Lead(url="https://www.adzuna.co.uk/jobs/land/ad/2", title="Backend Software Engineer", company="Freetrade", location="London", date=None, snippet="")  # fmt: skip
        serper = FakeSerper({"Freetrade": [Result("https://jobs.ashbyhq.com/freetrade/9", "Senior Backend Software Engineer", "Freetrade", None, 1)]})  # fmt: skip
        assert resource.original(lead, serper=serper, sites=[], gl="gb", hl="en", exclude=[]) is None

    def test_an_aggregator_is_never_the_original_and_the_employers_system_is_preferred(self):
        lead = feeds.Lead(url="https://www.adzuna.co.uk/jobs/land/ad/3", title="Junior Software Engineer", company="Abound", location="London", date=None, snippet="")  # fmt: skip
        serper = FakeSerper({"Abound": [
            Result("https://www.jobleads.com/gb/job/junior-software-engineer-abound", "Junior Software Engineer - Abound", "", None, 1),
            Result("https://builtinlondon.uk/job/junior-software-engineer/11991410", "Junior Software Engineer at Abound", "Abound, London", None, 2),
            Result("https://jobs.ashbyhq.com/Abound/582538c4-9b6e-464f-932e-eb4a4337bc7b", "Junior Software Engineer", "Abound", None, 3),
        ]})  # fmt: skip
        hit = resource.original(lead, serper=serper, sites=[], gl="gb", hl="en", exclude=[])
        assert hit is not None and "ashbyhq.com" in hit.url

    def test_an_employer_page_with_only_a_slug_is_the_original_of_a_lead_it_spells(self):
        """16 Nov: Adzuna's Zürich roles were lost because Cudos AG's page is `/de/jobs/<slug>/`,
        which reads as a category with nothing else to go on. The lead is the something else."""
        lead = feeds.Lead(url="https://www.adzuna.ch/details/1", title="Junior Software Engineer im Cross-Company-Programm", company="Cudos AG", location="Zürich", date=None, snippet="")  # fmt: skip
        serper = FakeSerper({"Cudos": [
            Result("https://cudos.ch/de/jobs/junior-software-engineer-cudos-trail/", "Junior Software Engineer im Cross-Company-Programm", "Cudos AG Zürich", None, 1),
        ]})  # fmt: skip
        hit = resource.original(lead, serper=serper, sites=[], gl="ch", hl="en", exclude=[])
        assert hit is not None and hit.url.startswith("https://cudos.ch/de/jobs/")
        # A category page is still a category, whatever the lead: its slug doesn't spell the job.
        category = FakeSerper({"Cudos": [Result("https://cudos.ch/de/jobs/informatik/", "Junior Software Engineer im Cross-Company-Programm", "Cudos AG", None, 1)]})  # fmt: skip
        assert resource.original(lead, serper=category, sites=[], gl="ch", hl="en", exclude=[]) is None

    def test_a_different_job_at_the_same_company_isnt_it(self):
        serper = FakeSerper({"TD Bank": [Result("https://td.wd3.myworkdayjobs.com/en-US/x/job/London/Senior-Risk-Analyst_R9", "Senior Risk Analyst", "TD Bank", None, 1)]})  # fmt: skip
        assert resource.original(self.LEAD, serper=serper, sites=[], gl="gb", hl="en", exclude=[]) is None


def planned() -> list[PlannedQuery]:
    return [
        PlannedQuery(
            q='"Graduate Software Engineer" London -site:linkedin.com -site:indeed.com',
            gl="gb",
            hl="en",
            source="open",
            tier=1,
            title="Graduate Software Engineer",
        ),  # fmt: skip
    ]


class TestDiscoveringFromTheApis:
    def _profile(self, store):
        return store.get_profile().model_copy(update={"titles": ["Graduate Software Engineer", "Technology Graduate Programme"], "locations": ["London, UK"], "seniority": ["graduate"], "exclude": ["Senior"]})  # fmt: skip

    @respx.mock(assert_all_called=False)
    def test_full_text_postings_are_queued_to_be_read_from_the_api_and_leads_by_their_original(
        self, keys, reviewed, redis, store, respx_mock
    ):
        respx_mock.get(reed.SEARCH).mock(return_value=httpx.Response(200, json=fixture("reed_search")))
        respx_mock.get(adzuna.SEARCH.format(country="gb")).mock(return_value=httpx.Response(200, json=fixture("adzuna_search")))
        tracked = respx_mock.get(url__startswith="https://www.adzuna.co.uk/").mock(return_value=httpx.Response(200))
        serper = FakeSerper({'"Arch Capital Group"': [Result("https://boards.greenhouse.io/archcapital/jobs/42", "Technology & Change Graduate Programme 2027", "Arch Capital Group, London", None, 1)]})  # fmt: skip
        queue = TargetQueue(redis, "plan")

        found = strategist.discover_feeds(planned(), self._profile(store), queue=queue, known_urls=set(), sites=[], now=NOW, redis=redis, resource=serper)  # fmt: skip

        queued = queue.peek()
        assert [(t.via, t.source) for t in queued] == [("reed", "feed"), ("adzuna", "open")]
        assert queued[0].payload == {"jobId": 57544665}  # 14 Oct and 2 Oct are older than 21 days
        assert queued[1].url == "https://boards.greenhouse.io/archcapital/jobs/42"
        assert not tracked.called  # the tracked link is never requested
        assert found.by_feed["usajobs"]["skipped"].startswith("it has no jobs in GB")
        assert "person" in found.by_feed["careerjet"]["skipped"]
        assert len(serper.asked) == 1  # TD Bank's two leads are older than 21 days: never looked for

    @respx.mock(assert_all_called=False)
    def test_a_known_posting_or_an_excluded_title_isnt_queued(self, keys, reviewed, redis, store, respx_mock):
        respx_mock.get(reed.SEARCH).mock(return_value=httpx.Response(200, json=fixture("reed_search")))
        respx_mock.get(adzuna.SEARCH.format(country="gb")).mock(return_value=httpx.Response(200, json={"results": []}))
        queue = TargetQueue(redis, "plan")
        known = {"https://www.reed.co.uk/jobs/software-engineer/57544665"}
        strategist.discover_feeds(planned(), self._profile(store), queue=queue, known_urls=known, sites=[], now=NOW, redis=redis, resource=FakeSerper({}))  # fmt: skip
        assert queue.peek() == []


class TestTheBestLeadsFirst:
    """17 Nov: every API's leads are taken best match first, so `max_queued` and the searches for
    originals go to the best of them — and each posting carries the country it was found for."""

    @respx.mock(assert_all_called=False)
    def test_a_ranked_search_queues_the_best_full_text_posting_first(self, keys, reviewed, redis, store, respx_mock, monkeypatch):
        monkeypatch.setitem(wera().feeds, "reed", FeedConfig(terms_reviewed_on=date(2026, 11, 13), max_searches=3, max_queued=1))
        base = fixture("reed_search")["results"][0]
        two = [
            {**base, "jobId": 1, "jobTitle": "Java Developer", "date": "10/11/2026"},
            {**base, "jobId": 2, "jobTitle": "Graduate Software Engineer", "date": "10/11/2026"},
        ]
        respx_mock.get(reed.SEARCH).mock(return_value=httpx.Response(200, json={"results": two, "totalResults": 2}))
        respx_mock.get(adzuna.SEARCH.format(country="gb")).mock(return_value=httpx.Response(200, json={"results": []}))
        queue = TargetQueue(redis, "plan")
        profile = TestDiscoveringFromTheApis()._profile(store)
        prefer = lambda items: [1.0 if "Graduate" in title else 0.0 for title, _ in items]  # noqa: E731
        strategist.discover_feeds(planned(), profile, queue=queue, known_urls=set(), sites=[], now=NOW, redis=redis, resource=FakeSerper({}), rank=prefer)  # fmt: skip
        taken = queue.peek()
        # Reed answered the Java role first; with one place, the better match takes it.
        assert [t.title for t in taken] == ["Graduate Software Engineer"]
        assert taken[0].country == "gb"


class TestAnAgencyIsOneSource:
    """19 Nov: 15 of the 53 jobs on the list were eFinancialCareers reposts through Reed, fit 2–3.6.
    One a search now, and none while three of its jobs wait on the list."""

    def _discover(self, store, redis, respx_mock, monkeypatch, employer: str, **kw):
        monkeypatch.setitem(wera().feeds, "reed", FeedConfig(terms_reviewed_on=date(2026, 11, 13), max_searches=3, max_queued=6))
        base = fixture("reed_search")["results"][0]
        titles = ["Graduate Software Engineer", "Software Engineer", "Junior Software Engineer", "Software Developer"]
        four = [
            {**base, "jobId": i, "employerName": employer, "jobTitle": t, "date": "10/11/2026",
             "jobUrl": f"https://www.reed.co.uk/jobs/x/{i}"}
            for i, t in enumerate(titles, 1)
        ]  # fmt: skip
        respx_mock.get(reed.SEARCH).mock(return_value=httpx.Response(200, json={"results": four, "totalResults": 4}))
        respx_mock.get(adzuna.SEARCH.format(country="gb")).mock(return_value=httpx.Response(200, json={"results": []}))
        queue = TargetQueue(redis, "plan")
        profile = TestDiscoveringFromTheApis()._profile(store)
        found = strategist.discover_feeds(planned(), profile, queue=queue, known_urls=set(), sites=[], now=NOW, redis=redis, resource=FakeSerper({}), **kw)  # fmt: skip
        return queue.peek(), found

    @respx.mock(assert_all_called=False)
    def test_one_of_an_agencys_reposts_a_search(self, keys, reviewed, redis, store, respx_mock, monkeypatch):
        queued, found = self._discover(store, redis, respx_mock, monkeypatch, "eFinancialCareers")
        assert len(queued) == 1
        assert found.agency_reposts == 3

    @respx.mock(assert_all_called=False)
    def test_none_while_its_jobs_wait_on_the_list(self, keys, reviewed, redis, store, respx_mock, monkeypatch):
        queued, found = self._discover(store, redis, respx_mock, monkeypatch, "eFinancialCareers", crowded=frozenset({"efinancialcareers"}))  # fmt: skip
        assert queued == [] and found.agency_reposts == 4

    def test_three_waiting_on_the_list_is_crowded(self, store):
        from wera_agent.services.store import save_job

        waiting = store.list_jobs("new")[:3]
        for scored in waiting[:2]:
            save_job(store.s, store._job(store._job_row(scored.id)).model_copy(update={"company": "eFinancialCareers Ltd"}))
        assert store.crowded_agencies() == frozenset()
        save_job(store.s, store._job(store._job_row(waiting[2].id)).model_copy(update={"company": "eFinancialCareers"}))
        assert store.crowded_agencies() == frozenset({"efinancialcareers"})

    @respx.mock(assert_all_called=False)
    def test_an_employer_is_not_an_agency(self, keys, reviewed, redis, store, respx_mock, monkeypatch):
        queued, found = self._discover(store, redis, respx_mock, monkeypatch, "Wissen Technology")
        assert len(queued) == 4 and found.agency_reposts == 0


class TestOneSearchPerQuestion:
    @respx.mock(assert_all_called=False)
    def test_graduate_and_junior_are_one_reed_search_but_two_adzuna_ones(
        self, keys, reviewed, redis, store, respx_mock, monkeypatch
    ):
        for feed_id in feeds.FEEDS:
            monkeypatch.setitem(
                wera().feeds, feed_id, FeedConfig(terms_reviewed_on=date(2026, 11, 13), max_searches=3, max_queued=2)
            )
        reed_route = respx_mock.get(reed.SEARCH).mock(return_value=httpx.Response(200, json={"results": []}))
        adzuna_route = respx_mock.get(adzuna.SEARCH.format(country="gb")).mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        plan = [PlannedQuery(q=f'"{t}" London', gl="gb", hl="en", source="open", tier=1, title=t) for t in ("Graduate Software Engineer", "Junior Software Engineer")]  # fmt: skip
        # `only`, so London is the one place asked: `first` would ask the rest of the UK as well.
        profile = store.get_profile().model_copy(update={"locations": ["London, UK"], "seniority": ["graduate", "junior"], "city_scope": "only"})  # fmt: skip
        strategist.discover_feeds(plan, profile, queue=TargetQueue(redis, "plan"), known_urls=set(), sites=[], now=NOW, redis=redis, resource=FakeSerper({}))  # fmt: skip
        assert reed_route.call_count == 1  # both are "Software Engineer" at graduate level to Reed
        assert {c.request.url.params["title_only"] for c in adzuna_route.calls} == {"graduate", "junior"}


class TestHarvestingAFeedPosting:
    @respx.mock
    def test_a_reed_posting_is_read_from_the_api_and_says_who_found_it(self, keys, redis):
        respx.get(reed.DETAILS.format(id=57544665)).mock(return_value=httpx.Response(200, json=fixture("reed_job")))
        target = strategist.Target(
            "https://www.reed.co.uk/jobs/software-engineer/57544665", "reed.co.uk", "feed", "Reed: Graduate Software Engineer", 0,
            "Software Engineer", "", "2026-11-09", via="reed", payload={"jobId": 57544665},
        )  # fmt: skip
        out = harvest.harvest([target], caps=SearchCaps(max_queries=1, max_pages=1, max_credits=0), spend=SearchOutcome(), now=NOW, sites=[Site(domain="x.example", name="x")], redis=redis)  # fmt: skip
        [job] = out.jobs
        assert (job.via, job.company, job.sources[0].detail) == ("reed", "Wissen Technology", "reed.co.uk")
        assert job.salary is not None and job.salary.min == 40000
        assert out.by_path == {"reed": 1}


class TestTheSearcherHandedToTheRun:
    def _request(self, headers: dict[str, str]):
        from starlette.requests import Request

        return Request({"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})

    def test_what_the_web_proxy_forwards_is_kept_an_hour_for_that_run(self, outbound, redis, monkeypatch):
        from wera_agent.api import routes
        from wera_agent.sources import targets

        monkeypatch.setattr(targets, "connect", lambda: redis)
        routes._hand_person(self._request({"x-wera-person-ip": "203.0.113.7", "x-wera-person-ua": "Mozilla/5.0"}), ["run-1"])
        assert feeds.recall_person(redis, ["run-1"]) == feeds.Person(ip="203.0.113.7", user_agent="Mozilla/5.0")
        assert 0 < redis.ttl("wera:person:run-1") <= 3600

    def test_without_the_proxys_headers_there_is_no_person(self, outbound, redis, monkeypatch):
        from wera_agent.api import routes
        from wera_agent.sources import targets

        monkeypatch.setattr(targets, "connect", lambda: redis)
        routes._hand_person(self._request({"user-agent": "curl"}), ["run-2"])
        assert feeds.recall_person(redis, ["run-2"]) is None
