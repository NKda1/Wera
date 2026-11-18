"""SRCH-34/35: international organisations' own vacancies, and ReliefWeb's.

Every reader runs against what its organisation really answered on 16 Nov 2026
(`fixtures/organisations/`, trimmed to the parts the reader reads), so a platform that changes
shape fails here before it fails someone's search.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from wera_agent import config
from wera_agent.clock import iso
from wera_agent.config import Organisation
from wera_agent.domain import filters, grades
from wera_agent.schemas.jobs import Ats, InternationalPost, Job, JobSource, Location
from wera_agent.schemas.profile import ProfileChannels, SearchProfile
from wera_agent.settings import get_settings
from wera_agent.sources import ats_feeds, organisations
from wera_agent.sources.feeds import FeedQuery, reliefweb
from wera_agent.sources.provider_guard import Guard

FIXTURES = Path(__file__).parent / "fixtures" / "organisations"
NOW = 1_794_873_600_000  # 2026-11-16T12:00Z

UN = Organisation(id="un", name="United Nations", platform="un_careers", host="careers.un.org")
WHO = Organisation(id="who", name="WHO", platform="taleo", host="careers.who.int", section="ex", portal="101430233")
WIPO = Organisation(id="wipo", name="WIPO", platform="taleo", host="wipo.taleo.net", section="wp_2", portal="101430233")
IMF = Organisation(id="imf", name="IMF", platform="workday", host="imf.wd5.myworkdayjobs.com", tenant="imf", site="IMF")
ITU = Organisation(id="itu", name="ITU", platform="successfactors", host="jobs.itu.int")


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def data(name: str) -> dict:
    return json.loads(text(name))


class Allow:
    def allows(self, url: str) -> bool:
        return True

    def verdict(self, url: str) -> str:
        return "allowed"


class Refuse:
    def allows(self, url: str) -> bool:
        return False

    def verdict(self, url: str) -> str:
        return "disallowed"


class Unreadable(Refuse):
    def verdict(self, url: str) -> str:
        return "unreadable"


@pytest.fixture
def lists(outbound):
    """Every organisation's list, as it answered."""
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://careers.un.org/jobfeed").mock(return_value=httpx.Response(200, text=text("un_jobfeed.xml")))
        mock.post(url__regex=r"https://careers\.who\.int/careersection/rest/.*").mock(
            return_value=httpx.Response(200, json=data("who_list.json"))
        )
        mock.post(url__regex=r"https://wipo\.taleo\.net/careersection/rest/.*").mock(
            return_value=httpx.Response(200, json=data("wipo_list.json"))
        )
        mock.post(url__regex=r"https://imf\.wd5\.myworkdayjobs\.com/.*/jobs").mock(
            return_value=httpx.Response(200, json=data("imf_list.json"))
        )
        mock.get(url__regex=r"https://jobs\.itu\.int/search/.*").mock(
            return_value=httpx.Response(200, text=text("itu_search.html"))
        )
        mock.get("https://careers.un.org/api/public/opening/joV2/285621/en").mock(
            return_value=httpx.Response(200, json=data("un_jov2_285621.json"))
        )
        mock.get(url__regex=r"https://imf\.wd5\.myworkdayjobs\.com/wday/cxs/imf/IMF/job/.*").mock(
            return_value=httpx.Response(200, json=data("imf_job.json"))
        )
        mock.get(url__regex=r"https://jobs\.itu\.int/job/.*").mock(return_value=httpx.Response(200, text=text("itu_job.html")))
        yield mock


class TestTheLists:
    def test_the_un_feed_gives_level_family_duty_station_and_dates(self, lists):
        items = organisations.LISTS["un_careers"](UN)
        first = items[0]
        assert (first.job_id, first.title, first.location, first.grade) == ("285621", "Associate Data Scientist", "Vienna", "P-2")
        assert (first.posted_at, first.closes_at) == ("2026-10-21", "2026-11-19")
        assert first.url == "https://careers.un.org/jobSearchDescription/285621?language=en"
        assert all(i.location and not i.location.isupper() for i in items)  # "NEW YORK" reads "New York"

    def test_taleo_columns_are_read_by_what_they_hold(self, lists):
        """WHO puts the job number second and the grade fourth; WIPO the grade second and says who
        may apply. Read by position, one of the two would be wrong."""
        who = {i.job_id: i for i in organisations.LISTS["taleo"](WHO)}
        assert who["2604979"].title == "Staff Counsellor" and who["2604979"].grade == "P3"
        assert who["2604979"].location == "Cairo, Egypt" and who["2604979"].closes_at == "2026-12-07"
        assert who["2604701"].location == "Dar-es-Salaam, Tanzania, United Republic of"
        wipo = {i.job_id: i for i in organisations.LISTS["taleo"](WIPO)}
        assert wipo["26300-TA"].grade == "P2" and wipo["26300-TA"].recruitment == "International"
        assert wipo["26300-TA"].location == "Geneva, Switzerland"
        assert wipo["26300-TA"].url.endswith("/careersection/wp_2/jobdetail.ftl?lang=en&job=26300-TA")

    def test_workday_and_successfactors(self, lists):
        imf = organisations.LISTS["workday"](IMF)
        assert imf[0].job_id == "26-R9861" and imf[0].closes_at == "2026-12-04" and imf[0].location == "USA, Washington DC"
        itu = organisations.LISTS["successfactors"](ITU)
        assert itu[0].url.startswith("https://jobs.itu.int/job/") and itu[0].posted_at == "2026-11-16"


class TestTheFullText:
    def test_each_platforms_posting_is_read_whole(self, lists):
        for org, item, words in [
            (UN, organisations.Listed(job_id="285621", title="t", url="u", location="Vienna"), "Org. Setting and Reporting"),
            (IMF, organisations.Listed(job_id="x", title="t", url="u", location="USA, Washington DC", handle="/job/USA-Washington-DC/Economist-Program-2027_26-R9861"), "Economist Program"),
            (ITU, organisations.Listed(job_id="x", title="t", url="https://jobs.itu.int/job/Geneva-Study-Groups-Advisor/1372899255/", location="Geneva, Switzerland"), "INTERNATIONAL TELECOMMUNICATION UNION"),
        ]:  # fmt: skip
            body = organisations.DETAILS[org.platform](org, item)
            assert body and words in body and len(body) > 2000, org.id

    def test_a_taleo_page_gives_its_text_and_when_it_was_posted(self, outbound):
        item = organisations.Listed(job_id="26300-TA", title="t", url="https://wipo.taleo.net/careersection/wp_2/jobdetail.ftl?lang=en&job=26300-TA", location="Geneva")  # fmt: skip
        with respx.mock:
            respx.get(item.url).mock(return_value=httpx.Response(200, text=text("wipo_job.html")))
            body = organisations.DETAILS["taleo"](WIPO, item)
        assert body and "IMPORTANT NOTICE" in body and item.posted_at == "2026-11-10"

    def test_workday_and_itu_fill_in_dates_and_grade_the_list_left_out(self, lists):
        imf = organisations.Listed(job_id="x", title="t", url="u", location="USA, Washington DC", handle="/job/x")
        organisations.DETAILS["workday"](IMF, imf)
        assert (imf.posted_at, imf.closes_at, imf.location) == ("2026-09-28", "2026-12-05", "USA, Washington DC, United States")
        itu = organisations.Listed(job_id="x", title="t", url="https://jobs.itu.int/job/a/1/", location="Geneva")
        organisations.DETAILS["successfactors"](ITU, itu)
        assert (itu.grade, itu.closes_at) == ("P4", "2026-12-15")


class TestReadingOnlyWhatCouldBeShown:
    def test_only_what_the_filters_keep_is_read_and_every_id_is_listed(self, lists):
        wanted = {"Associate Data Scientist"}
        found = organisations.fetch_all([UN], wanted=lambda f: f.title in wanted, max_details=40, robots=Allow())
        assert [j.title for j in found.jobs] == ["Associate Data Scientist"]
        assert len(found.listed) == 17 and found.answered == {"un_careers:un"}
        job = found.jobs[0]
        assert job.international == InternationalPost(
            org="un", grade="P-2", recruitment="international", family=job.international.family
        )
        assert job.ats == Ats(vendor="un_careers", board_token="un", job_id="285621") and len(job.description) > 2000

    def test_the_cap_reads_the_newest_first(self, lists):
        found = organisations.fetch_all([UN], wanted=lambda f: True, max_details=1, robots=Allow())
        assert len(found.jobs) <= 1 and len(found.listed) == 17

    def test_an_organisation_that_doesnt_answer_costs_only_its_own(self, outbound):
        with respx.mock:
            respx.get("https://careers.un.org/jobfeed").mock(return_value=httpx.Response(503))
            respx.post(url__regex=r"https://imf.*/jobs").mock(return_value=httpx.Response(200, json=data("imf_list.json")))
            found = organisations.fetch_all([UN, IMF], wanted=lambda f: False, max_details=40, robots=Allow())
        assert found.answered == {"workday:imf"} and "United Nations" in found.errors[0]

    def test_robots_txt_is_obeyed(self, lists):
        found = organisations.fetch_all([UN], wanted=lambda f: True, max_details=40, robots=Refuse())
        assert not found.jobs and not found.answered
        assert found.errors == ["United Nations: its robots.txt disallows reading its vacancies"]
        # One that couldn't be read (Workday answered 429s on 16 Nov) is left alone, and says so.
        unread = organisations.fetch_all([UN], wanted=lambda f: True, max_details=40, robots=Unreadable())
        assert unread.errors == ["United Nations: its robots.txt couldn't be read, so it was left alone this run"]

    def test_a_job_still_listed_is_not_closed_and_one_gone_is(self, lists):
        found = organisations.fetch_all([UN], wanted=lambda f: False, max_details=40, robots=Allow())
        known = {
            "job-listed": Ats(vendor="un_careers", board_token="un", job_id="285621"),
            "job-gone": Ats(vendor="un_careers", board_token="un", job_id="999999"),
        }
        assert ats_feeds.closed_tokens(found, known) == ["job-gone"]


class TestGrades:
    @pytest.mark.parametrize(
        ("written", "code", "level", "who"),
        [
            ("P-2", "P-2", "entry", "international"),
            ("P3", "P-3", "mid", "international"),
            ("NOD", "NO-D", "senior", "local"),
            ("G5", "G-5", "entry", "local"),
            ("I-1", "I-1", "intern", "international"),
            ("D-1", "D-1", "director", "international"),
            ("Associate Officer (P2)", "P-2", "entry", "international"),
            ("Young Professionals Programme 2027", "YPP", "entry", "international"),
        ],
    )
    def test_a_grade_is_read_however_it_is_written(self, written, code, level, who):
        assert grades.parse(written) == grades.Grade(code, level, who)

    def test_no_grade_and_national_posts(self):
        assert grades.parse("No grade") is None and grades.parse("CON") is None
        assert grades.recruitment("National Consultant for SIMEX", None) == "local"
        assert grades.recruitment("Officer", grades.parse("P3"), stated="Local") == "local"


def profile(**over) -> SearchProfile:
    base = {
        "titles": ["Graduate Software Engineer", "Backend Engineer"],
        "skills": ["Python"],
        "locations": [],
        "countries": ["GB"],
        "remote": "any",
        "seniority": ["graduate", "junior", "entry"],
        "exclude": ["Senior"],
        "sites": [],
        "recency": "21d",
        "channels": ProfileChannels(board_search=True, inbox=True, watchlist=True),
        "generated_from_cv_version": 1,
        "updated_at": iso(NOW),
    }
    return SearchProfile.model_validate(base | over)


def post(title: str, where: str, *, grade: str | None = "P-2", recruitment: str | None = "international", family: str | None = None, posted: str | None = None, closes: str | None = None) -> Job:  # fmt: skip
    return Job(
        id="job-1",
        dedupe_key="k",
        title=title,
        company="United Nations",
        location=Location(city=where.split(",")[0], raw=where),
        remote="onsite",
        description_md="x" * 300,
        description_status="full",
        sources=[JobSource(channel="watchlist", detail="un", seen_at=iso(NOW))],
        language="en",
        posted_at=posted,
        posted_precision="exact",
        closes_at=closes,
        first_seen=iso(NOW),
        last_seen=iso(NOW),
        status="new",
        international=InternationalPost(org="un", grade=grade, recruitment=recruitment, family=family),
    )


class TestTheRulesForTheirPosts:
    def test_an_internationally_recruited_post_shows_anywhere_with_no_switch(self):
        """17 Nov: by relevance, not region — the title and grade rules decide, not the duty station."""
        geneva = post("Associate Information Systems Officer", "Geneva")
        assert filters.hidden_reason(geneva, profile(), NOW) is None
        legal = post("Associate Legal Officer", "Geneva", family="Legal")
        assert filters.hidden_reason(legal, profile(), NOW) is not None  # anywhere, but only relevant ones

    def test_a_locally_recruited_post_still_follows_the_countries(self):
        nairobi = post("Information Systems Assistant", "Nairobi", grade="G-5", recruitment="local")
        assert filters.hidden_reason(nairobi, profile(), NOW).startswith(filters.ELSEWHERE)

    def test_the_grade_decides_the_level(self):
        """ "Associate" and "Officer" say little; P-4 says seven years."""
        p4 = post("Information Systems Officer", "London", grade="P-4")
        assert filters.hidden_reason(p4, profile(), NOW) == "Grade is more senior than your search: P-4"
        assert filters.hidden_reason(p4, profile(seniority=["senior"]), NOW) is None
        intern = post("Intern - Software Development", "London", grade="I-1")
        assert filters.hidden_reason(intern, profile(), NOW) is None  # a graduate's search takes internships
        assert filters.hidden_reason(intern, profile(seniority=["junior"]), NOW).startswith("An internship")

    def test_their_titles_are_judged_by_the_kind_of_work(self):
        """The UN hires a software engineer as an "Associate Information Systems Officer"."""
        by_title = post("Associate Information Systems Officer", "London")
        by_family = post("Associate Programme Officer", "London", family="Information Management Systems and Technology")
        other = post("Associate Legal Officer", "London", family="Legal")
        assert filters.hidden_reason(by_title, profile(), NOW) is None
        assert filters.hidden_reason(by_family, profile(), NOW) is None
        assert filters.hidden_reason(other, profile(), NOW).startswith("Not one of your target titles")

    def test_a_post_open_to_its_deadline_is_not_too_old(self):
        """UN posts stay open four to eight weeks to a stated deadline, and aren't filled before it."""
        old = post("Associate Information Systems Officer", "London", posted="2026-10-01", closes="2026-12-01")
        assert filters.hidden_reason(old, profile(), NOW) is None
        closed = post("Associate Information Systems Officer", "London", posted="2026-10-01", closes="2026-11-01")
        assert filters.hidden_reason(closed, profile(), NOW).startswith("Posted outside")


class TestReliefWeb:
    @pytest.fixture
    def appname(self, outbound, monkeypatch):
        monkeypatch.setenv("RELIEFWEB_APPNAME", "wera-test")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    def answer(self, source_type: str = "International Organization") -> dict:
        return {
            "data": [
                {
                    "id": 1,
                    "fields": {
                        "title": "Associate Information Management Officer (P-2)",
                        "body": "UNHCR is seeking… " * 30,
                        "url_alias": "https://reliefweb.int/job/1/associate-information-management-officer",
                        "date": {"created": "2026-11-10T00:00:00+00:00", "closing": "2026-12-01T00:00:00+00:00"},
                        "source": [
                            {"name": "UN High Commissioner for Refugees", "shortname": "UNHCR", "type": {"name": source_type}}
                        ],
                        "country": [{"name": "Kenya", "iso3": "ken"}],
                        "city": [{"name": "Nairobi"}],
                        "career_categories": [{"name": "Information Management"}],
                    },
                }
            ]
        }

    def test_one_search_asks_the_countries_and_with_the_switch_any_international_organisation(self, appname):
        query = FeedQuery(title="Graduate Software Engineer", role="Software Engineer", level="graduate", city=None, country="gb")
        with respx.mock:
            route = respx.post("https://api.reliefweb.int/v2/jobs").mock(return_value=httpx.Response(200, json=self.answer()))
            leads, _ = reliefweb.search(query, Guard(None, "reliefweb"), countries=["gb", "ch"], international=True, early=True)
        sent = json.loads(route.calls[0].request.content)
        assert route.calls[0].request.url.params["appname"] == "wera-test"
        assert sent["query"] == {"value": "Software Engineer"}
        where = sent["filter"]["conditions"][-1]
        assert (
            where["operator"] == "OR"
            and {"field": "country.iso3", "value": ["CHE", "GBR"], "operator": "OR"} in where["conditions"]
        )
        assert {"field": "experience.name", "value": "0-2 years"} in sent["filter"]["conditions"]
        card = leads[0].payload["card"]
        assert card["international"]["grade"] == "P-2" and card["international"]["recruitment"] == "international"
        assert (card["company"], card["locationRaw"], card["closesAt"]) == (
            "UN High Commissioner for Refugees",
            "Nairobi, Kenya",
            "2026-12-01",
        )

    def test_an_ngo_is_not_an_international_organisation(self):
        card = reliefweb.card_of(self.answer("Non-governmental Organization")["data"][0]["fields"])
        assert "international" not in card

    def test_nothing_is_asked_without_an_appname(self, outbound):
        from wera_agent.sources import feeds

        assert "RELIEFWEB_APPNAME" in (feeds.missing_key("reliefweb") or "")


class TestTheGate:
    def test_every_shipped_organisation_waits_for_its_terms_to_be_read(self):
        config.organisations_file.cache_clear()  # another test may have read a different CONFIG_DIR
        shipped = config.organisations_file().organisations
        assert {o.id for o in shipped} >= {"un", "who", "wipo", "wto", "imf", "itu"}
        assert all(o.terms_reviewed_on is None for o in shipped)
        assert config.organisations() == []


class TestOnTheWatchlist:
    def test_the_watchlist_reads_reviewed_organisations_into_jobs(self, lists, monkeypatch):
        from wera_agent.graphs import intake

        monkeypatch.setattr(intake, "companies", lambda: [])
        monkeypatch.setattr(intake, "organisations", lambda: [UN])
        monkeypatch.setattr(organisations, "Robots", Allow)
        wants = profile(titles=["Data Scientist"], countries=["AT"])
        out = intake.from_watchlist(wants, NOW)
        assert [j.title for j in out.jobs] == ["Associate Data Scientist"]
        job = out.jobs[0]
        assert job.international is not None and job.international.grade == "P-2" and job.closes_at == "2026-11-19"
        assert job.sources[0].channel == "watchlist" and job.description_status == "full"
        assert out.feed is not None and len(out.feed.listed) == 17


class TestTheBestMatchesAreReadFirst:
    """17 Nov: of the postings that pass the profile, the ones read in full (at most `max_details`)
    are the best matches to the CV, not the newest."""

    def test_a_rank_decides_which_are_read(self, monkeypatch):
        from wera_agent.sources import organisations as org_feeds

        class Allow:
            def verdict(self, _url):
                return "allowed"

            def allows(self, _url):
                return True

        listed = [
            org_feeds.Listed(job_id="1", title="Associate Legal Officer", url="https://careers.un.org/1", location="Geneva, Switzerland", posted_at="2026-11-16"),
            org_feeds.Listed(job_id="2", title="Associate Data Scientist", url="https://careers.un.org/2", location="Vienna, Austria", posted_at="2026-11-01"),
        ]  # fmt: skip
        monkeypatch.setitem(org_feeds.LISTS, "un_careers", lambda _org: listed)
        read: list[str] = []
        monkeypatch.setitem(org_feeds.DETAILS, "un_careers", lambda _org, item: read.append(item.title) or "x" * 300)
        prefer_data = lambda items: [1.0 if "Data" in title else 0.0 for title, _ in items]  # noqa: E731

        org_feeds.fetch_all([UN], wanted=lambda _f: True, max_details=1, robots=Allow(), rank=prefer_data)
        assert read == ["Associate Data Scientist"]  # the older one, because it is the better match

        read.clear()
        org_feeds.fetch_all([UN], wanted=lambda _f: True, max_details=1, robots=Allow())
        assert read == ["Associate Legal Officer"]  # unranked: the newest, as before
