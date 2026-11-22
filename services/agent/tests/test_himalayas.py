"""SRCH-31 (22 Nov): Himalayas, remote jobs with their full text.

Its answer's documented shape (himalayas.app/api), with made-up jobs; nothing here reaches the
network. What matters, besides the query and the card: its terms (clause 113, read 22 Nov) — the
Apply is the job's `applicationLink`, unchanged, whatever a merge brings; the card credits
Himalayas with a link to the job there; and a job is closed once it expires, which the re-check
asks its status endpoint, never the job's page.
"""

from datetime import UTC, date, datetime, timedelta

import fakeredis
import httpx
import pytest
import respx

from wera_agent.clock import iso, parse_ms
from wera_agent.config import FeedConfig, Site, wera
from wera_agent.domain import dedupe
from wera_agent.nodes import strategist
from wera_agent.schemas.strategy import PlannedQuery
from wera_agent.services.store import save_job
from wera_agent.settings import get_settings
from wera_agent.sources import feeds, harvest, normalise
from wera_agent.sources.feeds import himalayas
from wera_agent.sources.firecrawl_search import SearchCaps, SearchOutcome
from wera_agent.sources.provider_guard import Guard
from wera_agent.sources.targets import TargetQueue

NOW = parse_ms("2026-11-22T12:00:00Z")
AT = datetime.fromtimestamp(NOW / 1000, tz=UTC)
GB = feeds.FeedQuery("Junior Data Analyst", "Data Analyst", "junior", "London", "gb")
APPLY = "https://acme.example/careers/data-analyst-42?utm_source=himalayas&ref=h"
PAGE = "https://himalayas.app/companies/acme/jobs/junior-data-analyst"
DESCRIPTION = (
    "<p>Acme is hiring a <strong>Junior Data Analyst</strong> to join its insights team.</p>"
    + (
        "<ul><li>Build dashboards in SQL and Python for the product and finance teams.</li>"
        "<li>Work with stakeholders to define metrics, and explain what the numbers say.</li></ul>"
    )
    * 3
)


def days_ago(n: float) -> int:
    return int((AT - timedelta(days=n)).timestamp())


def job_item(**over) -> dict:
    base = {
        "title": "Junior Data Analyst",
        "excerpt": "Acme is hiring a Junior Data Analyst.",
        "companyName": "Acme",
        "companySlug": "acme",
        "employmentType": "Full Time",
        "minSalary": 40000,
        "maxSalary": 50000,
        "currency": "GBP",
        "salaryPeriod": "annual",
        "seniority": ["Entry-level"],
        "locationRestrictions": [
            {"alpha2": "GB", "name": "United Kingdom", "slug": "united-kingdom"},
            {"alpha2": "NL", "name": "Netherlands", "slug": "netherlands"},
        ],
        "description": DESCRIPTION,
        "pubDate": days_ago(2),
        "expiryDate": int((AT + timedelta(days=30)).timestamp()),
        "applicationLink": APPLY,
        "guid": PAGE,
    }
    return base | over


def answer(*items: dict) -> dict:
    return {"updatedAt": days_ago(0), "offset": 0, "limit": 20, "totalCount": len(items), "jobs": list(items)}


@pytest.fixture
def key(outbound, monkeypatch):
    monkeypatch.setenv("HIMALAYAS_API_KEY", "test-himalayas")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def reviewed(monkeypatch):
    """Himalayas' terms reviewed, as config/wera.yaml would say once the user has read them."""
    monkeypatch.setitem(wera().feeds, "himalayas", FeedConfig(terms_reviewed_on=date(2026, 11, 22), max_searches=12))


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


class TestTheQuery:
    @respx.mock
    def test_the_role_in_the_country_at_entry_level_with_the_key_and_nothing_from_the_cv(self, key):
        route = respx.get(himalayas.SEARCH).mock(return_value=httpx.Response(200, json=answer(job_item())))
        leads, how = himalayas.search(GB, Guard(None, "himalayas"), early=True, now=AT)
        request = route.calls.last.request
        assert dict(request.url.params) == {"q": "Data Analyst", "country": "GB", "seniority": "Entry-level", "sort": "recent", "page": "1"}  # fmt: skip
        assert request.headers["authorization"] == "Bearer test-himalayas"
        assert (how, len(leads)) == ("fetched", 1)

    @respx.mock
    def test_no_seniority_when_the_person_wants_more_than_early_career(self, key):
        route = respx.get(himalayas.SEARCH).mock(return_value=httpx.Response(200, json=answer()))
        himalayas.search(GB, Guard(None, "himalayas"), early=False, now=AT)
        assert "seniority" not in route.calls.last.request.url.params


class TestTheCard:
    def test_a_remote_job_with_its_place_pay_dates_and_apply_link_unchanged(self):
        card = himalayas.card_of(job_item(), country="gb")
        assert card["remote"] == "remote"
        assert card["locationRaw"] == "Remote (United Kingdom, Netherlands)"
        assert card["country"] == "GB"
        assert card["salary"] == {"min": 40000.0, "max": 50000.0, "currency": "GBP", "period": "year"}
        assert card["postedAt"] == iso(days_ago(2) * 1000)
        assert card["closesAt"] == (AT + timedelta(days=30)).date().isoformat()
        assert card["url"] == APPLY  # their terms: every Apply is the applicationLink, as given
        assert card["viaUrl"] == PAGE
        assert "<" not in card["description"] and "- Build dashboards" in card["description"]

    def test_restrictions_as_plain_country_names_as_the_live_api_sends_them(self):
        """22 Nov, a live search: `locationRestrictions` came as ["United Kingdom"], not objects."""
        card = himalayas.card_of(job_item(locationRestrictions=["United Kingdom", "Netherlands"]), country="gb")
        assert (card["locationRaw"], card["country"]) == ("Remote (United Kingdom, Netherlands)", "GB")
        card = himalayas.card_of(job_item(locationRestrictions=["Germany"]), country="gb")
        assert card["country"] is None

    def test_a_job_open_anywhere_belongs_to_no_country(self):
        card = himalayas.card_of(job_item(locationRestrictions=[]), country="ch")
        assert (card["locationRaw"], card["country"]) == ("Remote (worldwide)", None)

    def test_a_weeks_pay_is_put_as_a_years_and_a_guid_that_isnt_a_page_gives_none(self):
        card = himalayas.card_of(
            job_item(minSalary=1000, maxSalary=None, salaryPeriod="weekly", guid="acme-junior-x"), country="gb"
        )
        assert card["salary"] == {"min": 52000.0, "max": None, "currency": "GBP", "period": "year"}
        assert card["viaUrl"] is None

    @respx.mock
    def test_old_and_expired_jobs_are_dropped_and_one_without_an_apply_link(self, key):
        items = [
            job_item(title="Fresh"),
            job_item(title="Old", pubDate=days_ago(22)),
            job_item(title="Expired", expiryDate=days_ago(1)),
            job_item(title="No link", applicationLink=""),
        ]
        respx.get(himalayas.SEARCH).mock(return_value=httpx.Response(200, json=answer(*items)))
        leads, _how = himalayas.search(GB, Guard(None, "himalayas"), early=True, now=AT)
        assert [lead.title for lead in leads] == ["Fresh"]
        assert leads[0].url == APPLY and leads[0].payload["card"]["url"] == APPLY


class TestTheGate:
    def test_not_asked_before_the_user_has_read_its_terms(self, key):
        # Only the user sets the date (SRCH-04); every test starts with every API unreviewed (conftest).
        assert wera().feeds["himalayas"].terms_reviewed_on is None
        assert "terms" in feeds.skipped("himalayas", countries={"gb"}, person=None)

    def test_the_missing_key_is_named(self, outbound, reviewed):
        assert feeds.skipped("himalayas", countries={"gb"}, person=None) == "HIMALAYAS_API_KEY not set in .env.local"

    def test_skipped_for_someone_who_doesnt_want_remote_work(self, key, reviewed, store):
        no = store.get_profile().model_copy(update={"remote": "no_remote"})
        anyway = store.get_profile().model_copy(update={"remote": "any"})
        assert feeds.skipped("himalayas", countries={"ch"}, person=None, profile=no) == "you said you don't want remote work"
        assert feeds.skipped("himalayas", countries={"ch"}, person=None, profile=anyway) is None
        assert feeds.skipped("himalayas", countries={"ch"}, person=None) is None  # the place picker's question

    def test_full_text_apis_come_first(self):
        kinds = [feed.full_text for feed in feeds.FEEDS.values()]
        assert kinds == sorted(kinds, reverse=True) and feeds.FEEDS["himalayas"].full_text


def planned(*titles: str) -> list[PlannedQuery]:
    return [
        PlannedQuery(q=f'"{t}" London', gl=gl, hl="en", source="open", tier=1, title=t) for t in titles for gl in ("gb", "ch")
    ]


class TestDiscovering:
    @staticmethod
    def one_job_per_country(request):
        country = request.url.params["country"]
        return httpx.Response(
            200, json=answer(job_item(title=f"Junior Data Analyst {country}", applicationLink=f"{APPLY}&c={country}"))
        )

    @respx.mock(assert_all_called=False)
    def test_one_search_a_role_and_country_queued_to_read_from_the_api(self, key, reviewed, redis, store, respx_mock):
        route = respx_mock.get(himalayas.SEARCH).mock(side_effect=self.one_job_per_country)
        profile = store.get_profile().model_copy(
            update={
                "titles": ["Junior Data Analyst", "Graduate Data Analyst"],
                "countries": ["GB", "CH"],
                "locations": ["London, UK"],
                "seniority": ["junior"],
                "remote": "any",
                "exclude": [],
            }
        )
        queue = TargetQueue(redis, "plan")
        titles = planned("Junior Data Analyst", "Graduate Data Analyst")
        found = strategist.discover_feeds(titles, profile, queue=queue, known_urls=set(), sites=[], now=NOW, redis=redis)
        # "Junior" and "Graduate" Data Analyst are one role: asked once in each country, never by city.
        assert sorted(c.request.url.params["country"] for c in route.calls) == ["CH", "GB"]
        assert found.by_feed["himalayas"]["searches"] == 2
        queued = {t.title: t for t in queue.peek()}
        gb, ch = queued["Junior Data Analyst GB"], queued["Junior Data Analyst CH"]
        assert (gb.source, gb.via, gb.url) == ("feed", "himalayas", f"{APPLY}&c=GB")
        assert gb.country == "gb"  # the job lists the UK
        assert ch.country is None  # it doesn't list Switzerland: it came back as open to anyone


class TestHarvesting:
    @respx.mock
    def test_read_from_the_card_with_its_apply_link_label_and_country(self, key, redis):
        card = himalayas.card_of(job_item(), country="gb")
        target = strategist.Target(APPLY, "acme.example", "feed", "Himalayas: Junior Data Analyst", 0, card["title"], "", card["postedAt"], via="himalayas", payload={"card": card}, country="gb")  # fmt: skip
        out = harvest.harvest([target], caps=SearchCaps(max_queries=1, max_pages=1, max_credits=0), spend=SearchOutcome(), now=NOW, sites=[Site(domain="x.example", name="x")], redis=redis)  # fmt: skip
        [job] = out.jobs
        assert (job.apply_url, job.via, job.via_url) == (APPLY, "himalayas", PAGE)
        assert (job.remote, job.location.city, job.location.country) == ("remote", None, "GB")
        assert job.closes_at == card["closesAt"]
        assert job.salary is not None and job.salary.max == 50000


def himalayas_job(now: int = NOW):
    card = himalayas.card_of(job_item(), country="gb")
    made = normalise.from_scrape(card, url=APPLY, site="acme.example", markdown=card["description"], now=now)
    return made.model_copy(update={"via": "himalayas", "via_url": PAGE, "location": made.location.model_copy(update={"city": None, "country": "GB"})})  # fmt: skip


def ats_copy(now: int = NOW):
    """The same posting, found on the employer's own job system."""
    card = {"title": "Junior Data Analyst", "company": "Acme", "locationRaw": "Remote", "description": "x" * 400}
    return normalise.from_scrape(card, url="https://boards.greenhouse.io/acme/jobs/4242", site="greenhouse.io", markdown="x" * 400, now=now)  # fmt: skip


class TestTheApplyLinkSurvivesAMerge:
    def test_either_way_round_the_himalayas_link_and_label_win(self):
        assert dedupe.link_rank(ats_copy()) > dedupe.link_rank(himalayas_job())  # it would win otherwise
        kept = dedupe.merge_sources(himalayas_job(), ats_copy())
        assert (kept.apply_url, kept.via, kept.via_url) == (APPLY, "himalayas", PAGE)
        taken = dedupe.merge_sources(ats_copy(), himalayas_job())
        assert (taken.apply_url, taken.via, taken.via_url) == (APPLY, "himalayas", PAGE)

    def test_saved_then_found_on_the_employers_system_it_still_applies_through_himalayas(self, store):
        [saved], _new = store.upsert_jobs([himalayas_job(store.now)])
        [stored], new = store.upsert_jobs([ats_copy(store.now)])
        assert stored.id == saved.id and not new  # one job: the same posting (NORM-06)
        assert (stored.apply_url, stored.via, stored.via_url) == (APPLY, "himalayas", PAGE)
        assert store._job(store._job_row(saved.id)).apply_url == APPLY


class TestExpiry:
    def test_its_expiry_date_closes_it(self, store):
        gone = himalayas_job(store.now).model_copy(update={"closes_at": iso(store.now - 3 * 86_400_000)[:10]})
        save_job(store.s, gone, questions=[])
        store.settle()
        assert store._job(store._job_row(gone.id)).status == "closed"

    @pytest.fixture
    def saved(self, outbound, store, redis, monkeypatch):
        from wera_agent.graphs import recheck

        job = himalayas_job(store.now)
        save_job(store.s, job, questions=[])
        store.s.commit()
        monkeypatch.setattr(recheck, "_guard", lambda provider: Guard(redis, provider))
        return job

    @respx.mock(assert_all_called=False)
    def test_the_status_endpoint_410_closes_it_and_the_page_is_never_fetched(self, key, saved, redis, respx_mock):
        from wera_agent.db.session import transaction
        from wera_agent.graphs import recheck
        from wera_agent.services.store import Store

        status = respx_mock.get("https://himalayas.app/jobs/api/companies/acme/jobs/junior-data-analyst").mock(
            return_value=httpx.Response(410, json={"expiryDate": days_ago(1)})
        )
        page = respx_mock.get(url__startswith="https://acme.example/").mock(return_value=httpx.Response(200))
        assert recheck.check_one(saved.id, force=True) == "gone"
        assert status.called and not page.called
        assert status.calls.last.request.headers["authorization"] == "Bearer test-himalayas"
        assert Guard(redis, "himalayas").used_today() == 1  # counted on the day's meter
        with transaction() as session:
            assert Store(session)._job(Store(session)._job_row(saved.id)).status == "closed"

    @respx.mock(assert_all_called=False)
    def test_a_200_confirms_it_open(self, key, saved, respx_mock):
        from wera_agent.db.session import transaction
        from wera_agent.graphs import recheck
        from wera_agent.services.store import Store

        respx_mock.get("https://himalayas.app/jobs/api/companies/acme/jobs/junior-data-analyst").mock(
            return_value=httpx.Response(200, json={"job": job_item()})
        )
        page = respx_mock.get(url__startswith="https://acme.example/").mock(return_value=httpx.Response(404))
        assert recheck.check_one(saved.id, force=True) == "open"
        assert not page.called
        with transaction() as session:
            stored = Store(session)._job(Store(session)._job_row(saved.id))
        assert stored.status == "new" and stored.last_confirmed_at is not None

    def test_without_its_page_on_himalayas_nothing_is_asked(self, redis):
        assert himalayas.status(None, Guard(redis, "himalayas")) == "unknown"
        assert himalayas.status_url(PAGE) == "https://himalayas.app/jobs/api/companies/acme/jobs/junior-data-analyst"


class TestTheStepLine:
    def test_himalayas_is_counted_like_the_others_and_not_asked_says_why(self):
        from collections import Counter

        from wera_agent.graphs import search_pipeline

        asked = strategist.FeedDiscovery(
            by_feed={
                "reed": {"searches": 3, "leads": 40, "queued": 4, "how": Counter()},
                "himalayas": {"searches": 2, "leads": 12, "queued": 5, "how": Counter()},
                "reliefweb": {"skipped": "its terms aren't reviewed yet (terms_reviewed_on in config/wera.yaml)"},
                "usajobs": {"skipped": "it has no jobs in GB"},
            },
            queued=9,
        )
        note = search_pipeline._feeds_note(asked)
        assert "(Reed 3, Himalayas 2)" in note
        assert "not asked: ReliefWeb (its terms aren't reviewed yet), USAJOBS (it has no jobs in GB)" in note
