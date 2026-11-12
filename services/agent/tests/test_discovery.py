"""SRCH-20, stages 1 and 2: the Strategist's queries, and discovery into the queue.

The queries must come from the parsed CV and the profile — a new CV, new queries — never quote
the place, never ask LinkedIn, and never ask a board nobody reviewed. Discovery must queue only
postings: a page of results is read for its posting links, everything else is dropped, and a
posting WERA already has never reaches the queue.
"""

from collections import Counter

import fakeredis
import pytest

from wera_agent.config import Site
from wera_agent.db.session import transaction
from wera_agent.nodes import strategist
from wera_agent.schemas.strategy import LocalTerm, Market, TargetMatrix
from wera_agent.services.store import Store
from wera_agent.sources.serper import Result
from wera_agent.sources.targets import TargetQueue

BOARDS = [
    Site(domain="builtinlondon.uk", name="Built In London", terms_reviewed_on="2026-01-01", countries=["GB"]),
    Site(domain="gradcracker.com", name="Gradcracker", terms_reviewed_on="2026-01-01", countries=["GB"]),
    Site(domain="unreviewed.example", name="Proposed", countries=["GB"], discovered_on="2026-11-01"),
    Site(domain="stepstone.de", name="StepStone", terms_reviewed_on="2026-01-01", countries=["DE"], languages=["de"]),
    Site(domain="declined.example", name="Declined", terms_reviewed_on="2026-01-01", declined_on="2026-11-02"),
]
TITLES = ["Graduate Software Engineer", "Junior Software Engineer", "Backend Engineer", "DevOps Engineer"]


def profile(store: Store, **over):
    base = {
        "titles": TITLES,
        "countries": ["GB"],
        "locations": ["London, UK"],
        "remote": "any",
        "sites": [s.domain for s in BOARDS],
    }
    return store.get_profile().model_copy(update=base | over)


class TestTheQueries:
    def test_tier_one_is_the_top_titles_open_and_on_every_reviewed_board(self, store):
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        tier1 = [(q.source, q.domain, q.q) for q in planned if q.tier == 1]
        assert tier1[:3] == [
            ("open", None, '"Graduate Software Engineer" job in "London" -site:linkedin.com'),
            ("board", "builtinlondon.uk", 'site:builtinlondon.uk "Graduate Software Engineer" job in "London"'),
            ("board", "gradcracker.com", 'site:gradcracker.com "Graduate Software Engineer" job in "London"'),
        ]
        assert {q.title for q in planned if q.tier == 1} == set(TITLES[:3])
        assert all((q.gl, q.hl) == ("gb", "en") for q in planned)

    def test_the_profiles_whole_place_is_never_an_exact_phrase(self, store):
        """`"London, UK"` as a phrase is why 11 of the last 12 Firecrawl searches came back empty.
        The city alone is quoted; the country is Google's `gl`."""
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        assert not any("London," in q.q or "UK" in q.q for q in planned)

    def test_open_searches_never_ask_linkedin(self, store):
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        assert all(q.q.endswith("-site:linkedin.com") for q in planned if q.source == "open")

    def test_only_reviewed_enabled_boards_serving_the_country(self, store):
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        assert {q.domain for q in planned if q.source == "board"} == {"builtinlondon.uk", "gradcracker.com"}

    def test_tier_two_pairs_titles_with_the_cvs_own_top_skills(self, store):
        cvp = store.cv_profile_cached()
        top = cvp.core_competencies.technical_stack[:2]
        planned = strategist.queries(profile(store), cvp, BOARDS)
        skilled = [q.q for q in planned if q.tier == 2 and q.source == "open" and any(s in q.q for s in top)]
        assert skilled and skilled[0] == f'"Graduate Software Engineer" {top[0]} job in "London" -site:linkedin.com'

    def test_a_search_is_written_as_a_person_asks_for_the_job(self, store):
        """Title, level, "job", and where: precise to the profile, not a bare title and a city."""
        planned = strategist.queries(profile(store, seniority=["graduate"]), None, BOARDS)
        assert '"Backend Engineer" graduate job in "London" -site:linkedin.com' in {q.q for q in planned}
        remote = strategist.queries(profile(store, seniority=["graduate"], remote="remote_only"), None, BOARDS)
        assert '"Backend Engineer" graduate remote job -site:linkedin.com' in {q.q for q in remote}

    def test_a_board_search_asks_for_postings_only_where_the_path_is_known(self, store):
        """Most of what Google holds for a board is its own search and category pages."""
        board = Site(
            domain="builtinlondon.uk",
            name="Built In",
            terms_reviewed_on="2026-01-01",
            countries=["GB"],
            posting_pattern=r"^/job/[^/]+/\d+",
        )
        planned = strategist.queries(profile(store, sites=["builtinlondon.uk"]), None, [board])
        boards = {q.q for q in planned if q.source == "board" and q.tier == 1}
        assert 'site:builtinlondon.uk inurl:job "Graduate Software Engineer" job in "London"' in boards

    def test_a_title_without_a_level_asks_for_the_persons_level(self, store):
        """Measured live: `"Software Engineer" London` came back mostly Senior and Lead roles."""
        planned = strategist.queries(profile(store, seniority=["graduate", "junior"]), None, BOARDS)
        asked = {q.q for q in planned if q.source == "open"}
        assert '"Backend Engineer" graduate job in "London" -site:linkedin.com' in asked
        assert '"Graduate Software Engineer" job in "London" -site:linkedin.com' in asked  # it says so already

    def test_someone_open_to_mid_level_isnt_narrowed(self, store):
        planned = strategist.queries(profile(store, seniority=["junior", "mid"]), None, BOARDS)
        assert '"Backend Engineer" job in "London" -site:linkedin.com' in {q.q for q in planned}

    def test_tier_four_is_page_two_of_tier_one(self, store):
        planned = strategist.queries(profile(store), None, BOARDS)
        tier1 = [(q.q, q.gl, q.hl) for q in planned if q.tier == 1]
        tier4 = [(q.q, q.gl, q.hl) for q in planned if q.tier == 4]
        assert tier4 == tier1 and all(q.page == 2 for q in planned if q.tier == 4)

    def test_preferences_have_a_place_already(self, store):
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS, preferences=["fintech"])
        assert '"Graduate Software Engineer" fintech job in "London" -site:linkedin.com' in {q.q for q in planned}

    def test_tier_three_asks_in_the_local_language(self, store):
        matrix = TargetMatrix(
            field="backend",
            markets=[
                Market(
                    country="DE",
                    languages=["de", "en"],
                    search_terms=[LocalTerm(term="Softwareentwickler", language="de", title="Graduate Software Engineer")],
                    boards=["stepstone.de"],
                    proposed=[],
                )
            ],
            cv_version=1,
            profile_key="k",
            model="m",
            generated_at="t",
        )
        planned = strategist.queries(profile(store, countries=["DE"], locations=["Berlin"]), None, BOARDS, matrix)
        local = [(q.source, q.gl, q.hl, q.q) for q in planned if q.tier == 3]
        assert ("open", "de", "de", '"Softwareentwickler" "Berlin" -site:linkedin.com') in local
        assert ("board", "de", "de", 'site:stepstone.de "Softwareentwickler" "Berlin"') in local


class TestANewCvIsANewSearch:
    def test_a_different_cv_makes_new_queries_and_a_new_queue(self, client):
        with transaction() as session:
            store = Store(session)
            store.put_profile(store.get_profile().model_copy(update={"countries": ["GB"]}))
        assert client.post("/strategy").status_code == 200
        with transaction() as session:
            store = Store(session)
            before_key = store.plan_key()
            before = [q.q for q in store.strategy().queries]
            assert before, "a plan carries its queries"

            cv = store.cv()
            cv.skills = [cv.skills[-1], *cv.skills[:-1]]  # a different top skill
            store.put_cv(cv)
        with transaction() as session:
            store = Store(session)
            assert store.strategy() is None  # the old plan is for somebody else's search now
            assert store.plan_key() != before_key  # so its queue is never read
            after, _ = store.search_queries(refresh=False)
            assert [q.q for q in after] != before


class FakeSerper:
    """Serper's interface, answering from a table."""

    def __init__(self, answers: dict[str, list[Result]]):
        self.answers, self.sent, self.answered, self.errors, self.stopped = answers, 0, 0, [], None
        self.asked: list[str] = []

    @property
    def credits_used(self) -> int:
        return self.answered

    def search(self, q: str, *, gl: str, hl: str, page: int = 1) -> list[Result] | None:
        self.asked.append(q)
        self.sent += 1
        self.answered += 1
        return self.answers.get(q, [])


class AllowAll:
    def allows(self, _url: str) -> bool:
        return True


def result(url: str, title: str = "A job", n: int = 1) -> Result:
    return Result(url=url, title=title, snippet="", date=None, position=n)


LISTING_HTML = """
<a href="/job/backend-engineer/12000001">Backend Engineer</a>
<a href="/job/backend-engineer/12000001?utm_source=x">Backend Engineer (again)</a>
<a href="/jobs/remote">Remote jobs</a>
<a href="https://elsewhere.example/job/x/99999999">An advert</a>
"""


class TestDiscovery:
    @pytest.fixture
    def queue(self):
        return TargetQueue(fakeredis.FakeRedis(decode_responses=True), "5-abc")

    def run(self, store, queue, answers, *, known=frozenset(), tiers=(1,), fetch=None):
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        serper = FakeSerper({planned[0].q: answers})
        out = strategist.discover(
            planned,
            tiers,
            serper=serper,
            queue=queue,
            known_urls=set(known),
            sites=BOARDS,
            robots=AllowAll(),
            fetch=fetch or (lambda _u: None),
        )
        return out, serper, planned

    def test_only_postings_are_queued(self, store, queue):
        out, _, _ = self.run(
            store,
            queue,
            [
                result("https://builtinlondon.uk/job/graduate-software-engineer/12138050", "Graduate SWE", 1),
                result("https://www.gradcracker.com/search/technology-it/graduate-jobs?page=2", "Graduate jobs", 2),
                result("https://www.gradcracker.com/hub/1/x/graduate-job/86762/swe", "Not Found", 3),
                result("https://builtinlondon.uk/company/shield-5", "Company", 4),
            ],
        )
        assert out.kinds == Counter({"posting": 1, "listing": 1, "closed": 1, "other": 1})
        assert [t.url for t in queue.peek()] == ["https://builtinlondon.uk/job/graduate-software-engineer/12138050"]

    def test_a_page_of_results_is_read_for_its_posting_links(self, store, queue):
        listing = "https://builtinlondon.uk/jobs?search=backend"
        out, _, _ = self.run(store, queue, [result(listing, "Backend jobs")], fetch=lambda url: LISTING_HTML)
        assert (out.listings_read, out.listing_links) == (1, 1)
        assert [t.url for t in queue.peek()] == ["https://builtinlondon.uk/job/backend-engineer/12000001"]

    def test_a_posting_wera_already_has_is_not_queued(self, store, queue):
        url = "https://builtinlondon.uk/job/graduate-software-engineer/12138050"
        out, _, _ = self.run(store, queue, [result(url)], known={url.replace("https://", "https://www.")})
        assert (out.known, queue.size()) == (1, 0)

    def test_a_declined_board_never_reaches_the_queue(self, store, queue):
        out, _, _ = self.run(store, queue, [result("https://declined.example/jobs/engineer-1234567")])
        assert (out.blocked, queue.size()) == (1, 0)

    def test_only_the_tiers_asked_for_are_sent(self, store, queue):
        _, serper, planned = self.run(store, queue, [])
        assert len(serper.asked) == sum(1 for q in planned if q.tier == 1)

    def test_a_title_the_profile_excludes_isnt_queued(self, store, queue):
        """MATCH-01 would hide it after it was read; reading it would spend one of the 8–12."""
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        out = strategist.discover(
            planned,
            [1],
            serper=FakeSerper(
                {
                    planned[0].q: [
                        result("https://builtinlondon.uk/job/senior-backend-engineer/9939160", "Senior Backend Engineer - Omaze"),
                        result("https://builtinlondon.uk/job/graduate-swe/12138050", "Graduate Software Engineer"),
                    ]
                }
            ),
            queue=queue,
            known_urls=set(),
            sites=BOARDS,
            profile=profile(store, exclude=["Senior"]),
            robots=AllowAll(),
        )
        assert out.excluded == 1
        assert [t.title for t in queue.peek()] == ["Graduate Software Engineer"]

    def test_a_title_thats_not_a_target_isnt_queued(self, store, queue):
        """Seen live: a `site:` search of a board for "Backend Engineer" London answered with an
        administrative assistant and a growth associate. MATCH-01 hid both after paying to read."""
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        out = strategist.discover(
            planned,
            [1],
            serper=FakeSerper(
                {
                    planned[0].q: [
                        result("https://builtinlondon.uk/job/admin/12000002", "Administrative Assistant – Bloodstock & Equine"),
                        result("https://builtinlondon.uk/job/backend/12000003", "Backend Engineer - Monzo"),
                        result("https://builtinlondon.uk/job/backend-ii/12000004", "Apply"),  # says nothing: kept
                    ]
                }
            ),
            queue=queue,
            known_urls=set(),
            sites=BOARDS,
            profile=profile(store),
            robots=AllowAll(),
        )
        assert out.excluded == 1
        assert [t.title for t in queue.peek()] == ["Backend Engineer - Monzo", "Apply"]

    def test_a_host_that_refuses_a_listing_isnt_asked_again(self, store, queue):
        """Measured live: Gradcracker answers plain HTTP with a bot check, and eight results pages
        in a row went to it. A refusal is final for the pass, and never worked around."""
        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        asked: list[str] = []

        def refuse(url: str) -> str:
            asked.append(url)
            raise strategist.Refused("HTTP 403")

        pages = [result(f"https://www.gradcracker.com/search/technology-it/graduate-jobs?page={n}") for n in range(2, 6)]
        out = strategist.discover(
            planned,
            [1],
            serper=FakeSerper({planned[0].q: pages}),
            queue=queue,
            known_urls=set(),
            sites=BOARDS,
            robots=AllowAll(),
            fetch=refuse,
        )
        assert len(asked) == 1 and out.refused == {"gradcracker.com"}

    def test_a_listing_robots_disallows_is_not_read(self, store, queue):
        class DenyAll:
            def allows(self, _url: str) -> bool:
                return False

        planned = strategist.queries(profile(store), store.cv_profile_cached(), BOARDS)
        fetched: list[str] = []
        out = strategist.discover(
            planned,
            [1],
            serper=FakeSerper({planned[0].q: [result("https://builtinlondon.uk/jobs?search=x")]}),
            queue=queue,
            known_urls=set(),
            sites=BOARDS,
            robots=DenyAll(),
            fetch=lambda u: fetched.append(u) or LISTING_HTML,
        )
        assert fetched == [] and out.listings_read == 0


class TestStageOneIsPaidForOnce:
    """What the scheduler used to check before every scheduled search, now stage 1's own rule."""

    def test_a_current_plan_is_not_paid_for_again(self, client, monkeypatch):
        with transaction() as session:
            store = Store(session)
            store.put_profile(store.get_profile().model_copy(update={"countries": ["GB"]}))
        assert client.post("/strategy").status_code == 200
        monkeypatch.setattr(Store, "refresh_strategy", lambda self: pytest.fail("refreshed a current plan"))
        with transaction() as session:
            planned, matrix = Store(session).search_queries()
        assert matrix is not None and planned

    def test_a_plan_that_cant_be_made_still_searches(self, store, monkeypatch):
        """Budget reached, provider down: the search runs in the person's own words."""
        from wera_agent.errors import ApiError

        def spent(_self):
            raise ApiError(402, "BUDGET_REACHED", "spent")

        monkeypatch.setattr(Store, "refresh_strategy", spent)
        planned, matrix = store.search_queries()
        assert matrix is None and planned
