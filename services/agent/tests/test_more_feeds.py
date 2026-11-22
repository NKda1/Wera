"""SRCH-31/32 (22 Nov): Arbeitnow, The Muse and Jooble.

Their answers' shapes as their API pages and public examples give them, with made-up jobs; nothing
here reaches the network. What matters: Arbeitnow has no search, so its feed is read through the
guard's day-long cache and matched here; The Muse is asked by category, level and place, its
optional key never part of a shared answer; Jooble's key is in its URL and never in Redis, and its
tracked link is never requested — its leads take the lookups each ticked country's floor first,
and the rest are previews. None of them runs before the user has read its terms.
"""

import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta

import fakeredis
import httpx
import pytest
import respx

from wera_agent.clock import iso, parse_ms
from wera_agent.config import FeedConfig, wera
from wera_agent.nodes import strategist
from wera_agent.schemas.strategy import PlannedQuery
from wera_agent.settings import get_settings
from wera_agent.sources import feeds, harvest, normalise
from wera_agent.sources.feeds import arbeitnow, jooble, themuse
from wera_agent.sources.provider_guard import ANSWERS, Guard
from wera_agent.sources.targets import Target, TargetQueue

NOW = parse_ms("2026-11-22T12:00:00Z")
AT = datetime.fromtimestamp(NOW / 1000, tz=UTC)
TEXT = "<p>Join our <strong>analytics</strong> team.</p>" + "<ul><li>Build dashboards in SQL and Python for finance.</li></ul>" * 6  # fmt: skip


def days_ago(n: float) -> int:
    return int((AT - timedelta(days=n)).timestamp())


@pytest.fixture
def redis():
    return fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture
def reviewed(monkeypatch):
    """The three APIs' terms reviewed, as config/wera.yaml would say once the user has read them."""
    day = date(2026, 11, 22)
    monkeypatch.setitem(wera().feeds, "arbeitnow", FeedConfig(terms_reviewed_on=day, max_pages=10, max_leads=40))
    monkeypatch.setitem(wera().feeds, "themuse", FeedConfig(terms_reviewed_on=day, max_searches=4))
    monkeypatch.setitem(wera().feeds, "jooble", FeedConfig(terms_reviewed_on=day, max_searches=12))


@pytest.fixture
def keys(outbound, monkeypatch):
    monkeypatch.setenv("JOOBLE_API_KEY", "test-jooble")
    monkeypatch.setenv("SERPER_API_KEY", "test-serper")
    monkeypatch.delenv("THEMUSE_API_KEY", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def profile_of(store, **over):
    base = {
        "titles": ["Graduate Data Analyst"],
        "countries": ["GB", "DE"],
        "locations": ["London, UK"],
        "seniority": ["graduate"],
        "remote": "any",
        "exclude": [],
    }
    return store.get_profile().model_copy(update=base | over)


def planned(title: str, *gls: str) -> list[PlannedQuery]:
    return [PlannedQuery(q=f'"{title}"', gl=gl, hl="en", source="open", tier=1, title=title) for gl in gls]


class TestTheGate:
    @pytest.mark.parametrize("feed_id", ["arbeitnow", "themuse", "jooble"])
    def test_not_asked_before_the_user_has_read_its_terms(self, keys, feed_id):
        assert wera().feeds[feed_id].terms_reviewed_on is None  # only the user sets it (SRCH-04)
        country = "us" if feed_id == "themuse" else "de"
        assert "terms" in feeds.skipped(feed_id, countries={country}, person=None)

    def test_jooble_without_its_key_is_skipped_and_says_so(self, outbound, reviewed, monkeypatch):
        monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
        get_settings.cache_clear()
        assert feeds.skipped("jooble", countries={"ch"}, person=None) == "JOOBLE_API_KEY not set in .env.local"

    def test_arbeitnow_needs_no_key_and_the_muses_is_optional(self, keys, reviewed):
        assert feeds.missing_key("arbeitnow") is None and feeds.missing_key("themuse") is None
        assert feeds.skipped("arbeitnow", countries={"nl"}, person=None) is None
        assert feeds.skipped("themuse", countries={"us"}, person=None) is None

    def test_each_only_where_it_has_jobs(self, keys, reviewed):
        assert feeds.skipped("arbeitnow", countries={"us"}, person=None).startswith("it has no jobs in US")
        assert feeds.skipped("themuse", countries={"gb"}, person=None).startswith("it has no jobs in GB")
        assert feeds.skipped("jooble", countries={"ch", "nl"}, person=None) is None

    def test_full_text_apis_come_first(self):
        kinds = [feed.full_text for feed in feeds.FEEDS.values()]
        assert kinds == sorted(kinds, reverse=True)
        assert feeds.FEEDS["arbeitnow"].full_text and feeds.FEEDS["themuse"].full_text
        assert not feeds.FEEDS["jooble"].full_text


# -- Arbeitnow -------------------------------------------------------------------------------------


def arbeit(title: str, *, location: str = "Berlin", remote: bool = False, age: float = 1, n: int = 0) -> dict:
    slug = f"{title.lower().replace(' ', '-')}-{n}"
    return {
        "slug": slug,
        "company_name": f"Firma {n}",
        "title": title,
        "description": TEXT,
        "remote": remote,
        "url": f"https://www.arbeitnow.com/jobs/companies/firma-{n}/{slug}",
        "tags": ["Data"],
        "job_types": ["full time"],
        "location": location,
        "created_at": days_ago(age),
    }


def arbeit_page(rows: list[dict], *, page: int, last: bool) -> dict:
    base = "https://www.arbeitnow.com/api/job-board-api"
    return {
        "data": rows,
        "links": {"first": f"{base}?page=1", "last": None, "prev": None, "next": None if last else f"{base}?page={page + 1}"},
        "meta": {"current_page": page, "per_page": 100},
    }


def read_arbeitnow(guard: Guard, **over):
    kw = {"targets": ["Data Analyst"], "countries": ["de"], "remote_ok": True, "max_pages": 10, "max_leads": 40, "now": AT}
    return arbeitnow.read(guard, **(kw | over))


class TestArbeitnow:
    @respx.mock
    def test_pages_are_read_until_the_last_and_kept_a_day_so_the_next_search_is_free(self, outbound, redis):
        pages = {
            "1": arbeit_page([arbeit("Data Analyst (m/w/d)", n=1), arbeit("Sales Manager", n=2)], page=1, last=False),
            "2": arbeit_page([arbeit("Junior Data Analyst", n=3)], page=2, last=True),
        }
        route = respx.get(arbeitnow.FEED).mock(side_effect=lambda r: httpx.Response(200, json=pages[r.url.params["page"]]))
        first = read_arbeitnow(Guard(redis, "arbeitnow"))
        assert route.call_count == 2  # page 2 had no `links.next`: page 3 is never asked
        assert (first.how, first.read, [lead.title for lead in first.leads]) == ("fetched", 3, ["Data Analyst (m/w/d)", "Junior Data Analyst"])  # fmt: skip
        again = read_arbeitnow(Guard(redis, "arbeitnow"))
        assert route.call_count == 2 and again.how == "cached"  # the day's second search costs nothing
        assert [lead.url for lead in again.leads] == [lead.url for lead in first.leads]
        assert wera().guard.cache_minutes_by["arbeitnow"] == 1440
        ttls = [redis.ttl(k) for k in redis.keys(f"{ANSWERS}arbeitnow:*")]
        assert len(ttls) == 2 and all(86_000 < t <= 86_400 for t in ttls)

    @respx.mock
    def test_an_empty_page_stops_it_and_max_pages_bounds_it(self, outbound):
        route = respx.get(arbeitnow.FEED).mock(return_value=httpx.Response(200, json=arbeit_page([], page=1, last=False)))
        assert read_arbeitnow(Guard(None, "arbeitnow")).read == 0 and route.call_count == 1
        full = arbeit_page([arbeit("Data Analyst", n=1)], page=1, last=False)
        route.mock(return_value=httpx.Response(200, json=full))
        read_arbeitnow(Guard(None, "arbeitnow"), max_pages=3)
        assert route.call_count == 1 + 3

    @respx.mock
    def test_matched_here_to_the_titles_and_the_ticked_countries(self, outbound):
        rows = [
            arbeit("Data Analyst", location="München", n=1),
            arbeit("Data Analyst", location="Paris", n=2),  # France isn't ticked
            arbeit("Werkstudent Data Analyst (m/w/d)", location="Hamburg", n=3),
            arbeit("Buchhalter", location="Berlin", n=4),  # not one of the titles
            arbeit("Data Analyst", location="Köln", age=30, n=5),  # older than 21 days
        ]
        respx.get(arbeitnow.FEED).mock(return_value=httpx.Response(200, json=arbeit_page(rows, page=1, last=True)))
        got = read_arbeitnow(Guard(None, "arbeitnow"))
        assert [lead.payload["card"]["company"] for lead in got.leads] == ["Firma 1", "Firma 3"]
        assert got.read == 5

    @respx.mock
    def test_a_remote_job_counts_anywhere_for_someone_open_to_remote_work_only(self, outbound):
        rows = [arbeit("Data Analyst", location="Paris", remote=True, n=1)]
        respx.get(arbeitnow.FEED).mock(return_value=httpx.Response(200, json=arbeit_page(rows, page=1, last=True)))
        [lead] = read_arbeitnow(Guard(None, "arbeitnow")).leads
        card = lead.payload["card"]
        assert (card["remote"], card["country"], card["locationRaw"]) == ("remote", None, "Remote (Paris)")
        assert read_arbeitnow(Guard(None, "arbeitnow"), remote_ok=False).leads == []

    @respx.mock
    def test_at_most_max_leads_and_the_uk_feed_only_with_gb_ticked(self, outbound):
        rows = [arbeit("Data Analyst", n=i) for i in range(30)]
        de = respx.get(arbeitnow.FEED).mock(return_value=httpx.Response(200, json=arbeit_page(rows, page=1, last=True)))
        uk = respx.get(arbeitnow.FEED_UK).mock(return_value=httpx.Response(200, json=arbeit_page([], page=1, last=True)))
        got = read_arbeitnow(Guard(None, "arbeitnow"), max_leads=5)
        assert len(got.leads) == 5 and got.read == 30 and de.called and not uk.called
        read_arbeitnow(Guard(None, "arbeitnow"), countries=["gb", "de"])
        assert uk.called

    def test_the_card_is_the_full_text_with_its_place_and_date(self):
        card = arbeitnow.card_of(arbeit("Data Analyst", location="Berlin", n=7), countries={"de"})
        assert (card["title"], card["company"], card["locationRaw"], card["country"]) == ("Data Analyst", "Firma 7", "Berlin", "DE")  # fmt: skip
        assert card["postedAt"] == iso(days_ago(1) * 1000)
        assert card["url"] == "https://www.arbeitnow.com/jobs/companies/firma-7/data-analyst-7"
        assert "<" not in card["description"] and "- Build dashboards" in card["description"]
        assert "remote" not in card

    @respx.mock(assert_all_called=False)
    def test_one_read_for_the_whole_search_queued_from_the_card_and_counted_as_read(self, keys, reviewed, redis, store, respx_mock):  # fmt: skip
        rows = [arbeit("Data Analyst", location="Berlin", n=1), arbeit("Koch", n=2), arbeit("Data Analyst", location="Wien", n=3)]  # fmt: skip
        route = respx_mock.get(arbeitnow.FEED).mock(return_value=httpx.Response(200, json=arbeit_page(rows, page=1, last=True)))  # fmt: skip
        respx_mock.get(arbeitnow.FEED_UK).mock(return_value=httpx.Response(200, json=arbeit_page([], page=1, last=True)))
        queue = TargetQueue(redis, "plan")
        found = strategist.discover_feeds(
            planned("Graduate Data Analyst", "gb", "de"),
            profile_of(store),
            queue=queue,
            known_urls=set(),
            sites=[],
            now=NOW,
            redis=redis,  # fmt: skip
        )
        assert route.call_count == 1
        stats = found.by_feed["arbeitnow"]
        assert (stats["searches"], stats["leads"], stats["read"]) == (1, 1, 3)  # Austria isn't ticked
        [target] = queue.peek()
        assert (target.source, target.via, target.country) == ("feed", "arbeitnow", "de")
        assert target.payload["card"]["title"] == "Data Analyst"

    def test_the_step_line_says_what_it_matched_of_what_it_read(self):
        from wera_agent.graphs import search_pipeline

        asked = strategist.FeedDiscovery(
            by_feed={
                "himalayas": {"searches": 2, "leads": 12, "queued": 5, "how": Counter()},
                "arbeitnow": {"searches": 1, "leads": 40, "read": 1000, "queued": 9, "how": Counter()},
                "jooble": {"skipped": "JOOBLE_API_KEY not set in .env.local"},
            },
            queued=14,
        )
        note = search_pipeline._feeds_note(asked)
        assert "(Himalayas 2, Arbeitnow 40 matched of 1,000 read)" in note
        assert "not asked: Jooble (JOOBLE_API_KEY not set in .env.local)" in note


# -- The Muse --------------------------------------------------------------------------------------

US_NYC = feeds.FeedQuery("Graduate Data Analyst", "Data Analyst", "graduate", "New York", "us")
US_ALL = feeds.FeedQuery("Graduate Data Analyst", "Data Analyst", "graduate", None, "us")


def muse(name: str, *, n: int = 0, where: tuple[str, ...] = ("New York, NY",)) -> dict:
    return {
        "id": 1000 + n,
        "name": name,
        "type": "external",
        "publication_date": "2026-11-20T15:04:05Z",
        "short_name": f"job-{n}",
        "model_type": "jobs",
        "contents": TEXT,
        "locations": [{"name": w} for w in where],
        "categories": [{"name": "Data and Analytics"}],
        "levels": [{"name": "Entry Level", "short_name": "entry"}],
        "tags": [],
        "refs": {"landing_page": f"https://www.themuse.com/jobs/acme/job-{n}"},
        "company": {"id": 7, "short_name": "acme", "name": "Acme Corp"},
    }


def muse_page(*items: dict, page: int = 1, count: int = 1) -> dict:
    return {"page": page, "page_count": count, "items_per_page": 20, "took": 5, "timed_out": False, "total": len(items), "results": list(items)}  # fmt: skip


class TestTheMuse:
    @respx.mock
    def test_asked_by_category_entry_level_and_the_city_as_the_muse_writes_it_two_pages(self, keys):
        route = respx.get(themuse.SEARCH).mock(
            side_effect=lambda r: httpx.Response(
                200, json=muse_page(muse(f"Data Analyst {r.url.params['page']}"), page=int(r.url.params["page"]), count=5)
            )  # fmt: skip
        )
        leads, how = themuse.search(US_NYC, Guard(None, "themuse"), interns=False, remote="any")
        assert route.call_count == 2  # pages 1 and 2, never more
        params = route.calls[0].request.url.params
        assert params.get_list("category") == ["Data and Analytics", "Data Science"]
        assert params.get_list("level") == ["Entry Level"]
        assert (params["location"], params["page"]) == ("New York, NY", "1")
        assert "api_key" not in params  # the key is optional
        assert route.calls[1].request.url.params["page"] == "2"
        assert (how, [lead.title for lead in leads]) == ("fetched", ["Data Analyst 1", "Data Analyst 2"])

    @respx.mock
    def test_one_page_when_there_is_only_one_and_internships_for_someone_who_wants_them(self, keys):
        route = respx.get(themuse.SEARCH).mock(return_value=httpx.Response(200, json=muse_page(muse("Data Analyst"))))
        themuse.search(US_ALL, Guard(None, "themuse"), interns=True, remote="any")
        assert route.call_count == 1
        params = route.calls.last.request.url.params
        assert params.get_list("level") == ["Entry Level", "Internship"]
        assert params["location"] == "Flexible / Remote"  # no city ticked: the whole country, remote

    def test_where_it_is_asked(self):
        assert themuse.location_for("San Francisco", remote="any") == "San Francisco, CA"
        assert themuse.location_for("Boston, MA", remote="any") == "Boston, MA"
        assert themuse.location_for("Springfield", remote="any") == "Flexible / Remote"
        assert themuse.location_for(None, remote="no_remote") is None  # all of the US, no remote filter
        assert themuse.categories_for("Financial Analyst") == ("Accounting and Finance",)
        assert themuse.categories_for("Software Engineer")[0] == "Software Engineering"
        assert themuse.categories_for("Barista") == ()

    @respx.mock
    def test_the_key_is_sent_when_set_and_never_part_of_the_shared_answer(self, keys, redis, monkeypatch):
        route = respx.get(themuse.SEARCH).mock(return_value=httpx.Response(200, json=muse_page(muse("Data Analyst"))))
        monkeypatch.setenv("THEMUSE_API_KEY", "muse-secret")
        get_settings.cache_clear()
        themuse.search(US_NYC, Guard(redis, "themuse"), interns=False, remote="any")
        assert route.calls.last.request.url.params["api_key"] == "muse-secret"
        monkeypatch.delenv("THEMUSE_API_KEY")
        get_settings.cache_clear()
        _leads, how = themuse.search(US_NYC, Guard(redis, "themuse"), interns=False, remote="any")
        assert how == "cached" and route.call_count == 1  # the same question, whoever's key asked it
        assert not any("muse-secret" in (redis.get(k) or "") or "muse-secret" in k for k in redis.keys("*"))

    def test_the_card(self):
        card = themuse.card_of(muse("Junior Data Analyst", n=3, where=("New York, NY", "Flexible / Remote")))
        assert (card["title"], card["company"]) == ("Junior Data Analyst", "Acme Corp")
        assert card["url"] == "https://www.themuse.com/jobs/acme/job-3"
        assert (card["postedAt"], card["locationRaw"]) == ("2026-11-20T15:04:05Z", "New York, NY; Flexible / Remote")
        assert (card["remote"], card["country"]) == ("remote", "US")
        assert "<" not in card["description"] and "- Build dashboards" in card["description"]
        on_site = themuse.card_of(muse("Data Analyst", where=("Chicago, IL",)))
        assert "remote" not in on_site and on_site["country"] == "US"

    @respx.mock(assert_all_called=False)
    def test_one_search_per_family_and_place_and_none_for_a_title_in_no_family(self, keys, reviewed, redis, store, respx_mock):  # fmt: skip
        route = respx_mock.get(themuse.SEARCH).mock(return_value=httpx.Response(200, json=muse_page(muse("Junior Data Analyst"))))  # fmt: skip
        profile = profile_of(store, titles=["Graduate Data Analyst", "Junior Data Analyst", "Barista"], countries=["US"], locations=["New York, US"])  # fmt: skip
        plan = [*planned("Graduate Data Analyst", "us"), *planned("Junior Data Analyst", "us"), *planned("Barista", "us")]
        found = strategist.discover_feeds(plan, profile, queue=TargetQueue(redis, "plan"), known_urls=set(), sites=[], now=NOW, redis=redis)  # fmt: skip
        asked = {(c.request.url.params.get("location"), c.request.url.params["page"]) for c in route.calls}
        assert asked == {("New York, NY", "1"), ("Flexible / Remote", "1")}  # Data Analyst once a place
        assert found.by_feed["themuse"]["searches"] == 2


# -- Jooble ----------------------------------------------------------------------------------------

GB = feeds.FeedQuery("Graduate Data Analyst", "Data Analyst", "graduate", "London", "gb")
CH = feeds.FeedQuery("Graduate Data Analyst", "Data Analyst", "graduate", None, "ch")


def jooble_rows(*titles: str, where: str = "London", start: int = 0) -> dict:
    return {
        "totalCount": len(titles),
        "jobs": [
            {
                "title": f"<b>{title}</b>",
                "location": where,
                "snippet": f"&nbsp;...{title}: build dashboards in SQL for our team...&nbsp;",
                "salary": "",
                "source": "employer.example",
                "type": "Full-time",
                "link": f"https://jooble.org/desc/{start + i}?ckey=data+analyst&rgn=1",
                "company": f"Company {start + i}",
                "updated": "2026-11-20T00:00:00.0000000",
                "id": start + i,
            }
            for i, title in enumerate(titles)
        ],
    }


class FakeSerper:
    def __init__(self):
        self.sent, self.cached, self.errors, self.asked = 0, 0, [], []
        self.max_queries = 15

    def search(self, q, *, gl, hl, page=1):
        self.asked.append((q, gl))
        self.sent += 1
        return []


class TestJooble:
    @respx.mock
    def test_a_post_with_the_title_and_place_and_the_key_only_in_the_path(self, keys, redis):
        route = respx.post(f"{jooble.ENDPOINT}test-jooble").mock(return_value=httpx.Response(200, json=jooble_rows("Data Analyst")))  # fmt: skip
        [lead], how = jooble.search(GB, Guard(redis, "jooble"))
        assert route.calls.last.request.method == "POST"
        assert json.loads(route.calls.last.request.content) == {"keywords": "Graduate Data Analyst", "location": "London, United Kingdom", "page": "1"}  # fmt: skip
        jooble.search(CH, Guard(redis, "jooble"))
        assert json.loads(route.calls.last.request.content)["location"] == "Switzerland"
        assert how == "fetched"
        assert (lead.title, lead.company, lead.date) == ("Data Analyst", "Company 0", "2026-11-20")
        assert "<" not in lead.snippet and "&nbsp;" not in lead.snippet
        assert lead.url.startswith("https://jooble.org/desc/")

    @respx.mock
    def test_a_lead_in_another_country_is_dropped(self, keys, redis):
        """22 Nov, live: jooble.org read "London" as London, KY, and filled Switzerland with
        "Switzerland County, IN"; a lead whose place can't be told is kept for the filters."""
        rows = {"jobs": [*jooble_rows("A", where="London, KY")["jobs"], *jooble_rows("B", where="United Kingdom", start=1)["jobs"],
                         *jooble_rows("C", where="Manchester", start=2)["jobs"]]}  # fmt: skip
        respx.post(f"{jooble.ENDPOINT}test-jooble").mock(return_value=httpx.Response(200, json=rows))
        leads, _how = jooble.search(GB, Guard(redis, "jooble"))
        assert [lead.title for lead in leads] == ["B", "C"]
        assert jooble._elsewhere("Switzerland County, IN", "ch")

    @respx.mock
    def test_the_answer_is_shared_without_the_key_and_the_key_never_reaches_redis(self, keys, redis, monkeypatch):
        first = respx.post(f"{jooble.ENDPOINT}test-jooble").mock(return_value=httpx.Response(200, json=jooble_rows("Data Analyst")))  # fmt: skip
        jooble.search(GB, Guard(redis, "jooble"))
        monkeypatch.setenv("JOOBLE_API_KEY", "another-key")
        get_settings.cache_clear()
        other = respx.post(f"{jooble.ENDPOINT}another-key").mock(return_value=httpx.Response(200, json=jooble_rows()))
        _leads, how = jooble.search(GB, Guard(redis, "jooble"))
        assert how == "cached" and first.call_count == 1 and not other.called
        assert not any("test-jooble" in k or "test-jooble" in (redis.get(k) or "") for k in redis.keys("*") if redis.type(k) == "string")  # fmt: skip

    def test_the_key_in_its_path_is_masked_in_every_log_line(self):
        from wera_agent.logging import redact

        line = 'HTTP Request: POST https://jooble.org/api/0a1b2c3d-secret "HTTP/1.1 200 OK"'
        assert redact(line) == 'HTTP Request: POST https://jooble.org/api/[redacted] "HTTP/1.1 200 OK"'

    def test_its_link_is_a_tracked_link_on_any_of_its_sites_and_the_harvest_never_requests_it(self):
        assert normalise.is_tracked_link("https://jooble.org/desc/123?ckey=x")
        assert normalise.is_tracked_link("https://uk.jooble.org/away/9")
        assert not normalise.is_tracked_link("https://notjooble.org/jobs/1")
        target = Target("https://jooble.org/desc/123", "jooble.org", "open", "Jooble: Data Analyst", 0, "Data Analyst", "", None, via="jooble")  # fmt: skip
        assert harvest._gate(target, rules=None, declined=set(), sites=[]) == "tracked link"  # type: ignore[arg-type]

    @respx.mock(assert_all_called=False)
    def test_leads_take_the_lookups_each_countrys_floor_first_and_the_rest_are_previews(self, keys, reviewed, redis, store, respx_mock):  # fmt: skip
        def answer(request):
            where = json.loads(request.content)["location"]
            if where == "Switzerland":
                return httpx.Response(200, json=jooble_rows("Data Analyst Zurich", "Data Analyst Basel", where="Zürich", start=50))  # fmt: skip
            return httpx.Response(200, json=jooble_rows("Data Analyst A", "Data Analyst B", "Data Analyst C", where="London"))  # fmt: skip

        respx_mock.post(f"{jooble.ENDPOINT}test-jooble").mock(side_effect=answer)
        respx_mock.get(url__startswith="https://www.arbeitnow.com/").mock(return_value=httpx.Response(200, json={"data": []}))
        respx_mock.get(url__startswith="https://www.arbeitnow.co.uk/").mock(return_value=httpx.Response(200, json={"data": []}))
        tracked = respx_mock.get(url__startswith="https://jooble.org/").mock(return_value=httpx.Response(200))
        serper = FakeSerper()
        profile = profile_of(store, titles=["Data Analyst"], countries=["GB", "CH"], seniority=["graduate"])
        found = strategist.discover_feeds(
            planned("Data Analyst", "gb", "ch"),
            profile,
            queue=TargetQueue(redis, "plan"),
            known_urls=set(),
            sites=[],
            now=NOW,
            redis=redis,
            resource=serper,
            lookups=2,  # fmt: skip
        )
        assert sorted(gl for _q, gl in serper.asked) == ["ch", "gb"]  # one lookup each: the floors first
        assert len(found.previews) == 5  # the three not looked for, and the two looked for with nothing found
        assert {p.feed for p in found.previews} == {"jooble"}
        assert all(normalise.is_tracked_link(p.lead.url) for p in found.previews)
        assert not tracked.called  # the tracked link is never requested
        assert {"London, United Kingdom", "Switzerland"} <= {json.loads(c.request.content)["location"] for c in respx_mock.calls if c.request.method == "POST"}  # fmt: skip
