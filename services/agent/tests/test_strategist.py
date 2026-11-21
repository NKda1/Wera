"""The Strategist: a target matrix per country, and the board search that uses it.

What these hold it to: it plans only for the person's countries and ranks only boards WERA knows
in markets they serve; its search terms can't smuggle query syntax in; a board it suggests must
answer over the network and then waits for review like any other (SRCH-04); and a matrix is used
only while the CV, profile and model it was built from still hold. How the plan becomes searches
is `test_discovery.py`.
"""

from datetime import date

import pytest
import yaml

from wera_agent import config
from wera_agent.config import Site
from wera_agent.db.session import transaction
from wera_agent.llm import demo
from wera_agent.nodes import strategist
from wera_agent.schemas.llm_outputs import StrategistOutput
from wera_agent.schemas.strategy import Market, TargetMatrix
from wera_agent.services.store import Store
from wera_agent.sources import site_discovery

TITLES = ["Software Engineer", "Backend Engineer"]
BOARDS = {"uk-board.example": ["GB"], "eu-board.example": ["DE", "GB"], "anywhere.example": []}


def _reply(**market) -> dict:
    base = {"country": "DE", "languages": ["de", "en"], "searchTerms": [], "boards": [], "proposedBoards": []}
    return {"field": "backend engineering", "markets": [base | market]}


def _checked(raw: dict) -> StrategistOutput:
    return StrategistOutput.model_validate(raw, context={"countries": {"DE", "GB"}, "boards": BOARDS, "titles": TITLES})


class TestTheReplyIsKeptToTheQuestion:
    def test_a_country_the_person_isnt_looking_in_is_dropped(self):
        assert _checked(_reply(country="FR")).markets == []

    def test_only_known_boards_serving_the_market_are_ranked(self):
        out = _checked(_reply(boards=["eu-board.example", "uk-board.example", "made-up.example", "anywhere.example"]))
        assert out.markets[0].boards == ["eu-board.example", "anywhere.example"]

    def test_search_terms_cant_carry_query_syntax_or_stray_from_the_titles(self):
        out = _checked(
            _reply(
                searchTerms=[
                    {"term": '"Softwareentwickler" OR (Dev)', "language": "de", "title": "Software Engineer"},
                    {"term": "Backend-Entwickler", "language": "de", "title": "Backend Engineer"},
                    {"term": "Datenanalyst", "language": "de", "title": "Data Analyst"},  # not a profile title
                    {"term": "Ingénieur logiciel", "language": "fr", "title": "Software Engineer"},  # not a DE language
                ]
            )
        )
        assert [t.term for t in out.markets[0].search_terms] == ["Softwareentwickler Dev", "Backend-Entwickler"]

    def test_a_proposal_must_be_a_new_well_formed_host_and_never_linkedin(self):
        proposed = [
            {"domain": "https://www.stepstone.de/jobs", "name": "StepStone", "sectors": [], "why": "Largest DE board."},
            {"domain": "uk-board.example", "name": "Known", "sectors": [], "why": "Already known."},
            {"domain": "linkedin.com", "name": "LinkedIn", "sectors": [], "why": "No."},
            {"domain": "not a host", "name": "Bad", "sectors": [], "why": "Bad."},
        ]
        out = _checked(_reply(proposedBoards=proposed))
        assert [p.domain for p in out.markets[0].proposed_boards] == ["www.stepstone.de"]


def _profile(store: Store, countries: list[str]):
    return store.get_profile().model_copy(update={"countries": countries, "titles": TITLES})


def _matrix(*markets: Market) -> TargetMatrix:
    return TargetMatrix(field="f", markets=list(markets), cv_version=1, profile_key="k", model="m", generated_at="t")


SITES = [
    Site(domain="uk-board.example", name="UK", terms_reviewed_on="2026-01-01", countries=["GB"]),
    Site(domain="eu-board.example", name="EU", terms_reviewed_on="2026-01-01", countries=["DE", "GB"]),
]


class TestProposals:
    def test_only_a_board_that_answers_reaches_the_review_queue(self, monkeypatch):
        """A board a model remembers might not exist. Here an unreachable one is a rejection, not a
        blip — unlike discovery, whose hosts came from real search results."""
        answers = {"real.example": True, "ghost.example": None, "closed.example": False}
        monkeypatch.setattr(site_discovery, "confirm", lambda c, _o: answers[c.domain])
        found = strategist.verified_proposals(
            [(d, d, ["law"], "GB") for d in answers], field="corporate law", today=date(2026, 11, 10)
        )
        assert [s.domain for s in found] == ["real.example"]
        site = found[0]
        assert site.terms_reviewed_on is None and site.proposed  # SRCH-04: unsearchable until reviewed
        assert (site.countries, site.sectors, site.matched) == (["GB"], ["law"], "Strategist: corporate law")


class TestTheMatrixOverTheApi:
    def test_nowhere_named_means_nothing_to_plan(self, client):
        with transaction() as session:
            store = Store(session)
            store.put_profile(store.get_profile().model_copy(update={"countries": [], "locations": ["Remote"]}))
        res = client.post("/strategy")
        assert res.status_code == 422 and res.json()["error"]["code"] == "NO_COUNTRIES"

    def test_countries_are_read_off_the_locations_when_none_are_ticked(self, store):
        """A real profile read `countries: []` and `locations: ["London, UK"]`. That has said where."""
        profile = store.get_profile()
        assert strategist.target_countries(
            profile.model_copy(update={"countries": [], "locations": ["London, UK", "Berlin"]})
        ) == [
            "GB",
            "DE",
        ]
        assert strategist.target_countries(profile.model_copy(update={"countries": [], "locations": ["Austin, TX"]})) == ["US"]
        assert strategist.target_countries(profile.model_copy(update={"countries": ["NL"], "locations": []})) == ["NL"]
        # A city outside the ticked countries adds its own: "Toronto" is a search of Canada.
        assert strategist.target_countries(profile.model_copy(update={"countries": ["GB"], "locations": ["Toronto"]})) == [
            "GB",
            "CA",
        ]

    def test_a_country_outside_the_markets_is_kept_but_not_planned_for(self, store):
        """17 Nov: Europe and North America for now. Busan is South Korea and Dubai the UAE — the
        gazetteer still knows them, and the profile keeps them — but no search goes there."""
        from wera_agent.domain import places

        profile = store.get_profile()
        assert places.where([], ["Busan"]).countries == ["KR"]
        assert strategist.target_countries(profile.model_copy(update={"countries": [], "locations": ["Busan"]})) == []
        assert strategist.target_countries(profile.model_copy(update={"countries": ["GB", "AU"], "locations": ["Dubai"]})) == [
            "GB"
        ]

    def test_a_refresh_is_kept_until_the_profile_changes(self, client):
        with transaction() as session:
            store = Store(session)
            # The demo's cities, cut to these countries: a named city adds its own (Zurich, CH).
            store.put_profile(store.get_profile().model_copy(update={"countries": ["GB", "DE"], "locations": ["London", "Berlin"]}))  # fmt: skip
        made = client.post("/strategy")
        assert made.status_code == 200, made.text
        assert {m["country"] for m in made.json()["markets"]} == {"GB", "DE"}
        assert client.get("/strategy").json() == made.json()

        with transaction() as session:
            store = Store(session)
            store.put_profile(store.get_profile().model_copy(update={"countries": ["GB"], "locations": ["London"]}))
        assert client.get("/strategy").json() is None  # a plan for a search nobody is running now

    def test_verified_proposals_are_appended_unreviewed(self, client, tmp_path, monkeypatch):
        # The repository's file with room in it: its own boards and proposals waiting for review can
        # fill `max_sites` (12 of 12 on 20 Nov), and a full file rightly takes no proposal.
        data = yaml.safe_load((config.CONFIG_DIR / "sites.yaml").read_text())
        data["max_sites"] = len(data["sites"]) + 5
        (tmp_path / "sites.yaml").write_text(yaml.safe_dump(data))
        monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
        config.sites_file.cache_clear()
        config.sites.cache_clear()
        real = demo._demo_strategy

        def with_a_proposal(context: dict) -> dict:
            out = real(context)
            out["markets"][0]["proposedBoards"] = [
                {"domain": "lawjobs.example", "name": "Law Jobs", "sectors": ["law"], "why": "x"}
            ]
            return out

        monkeypatch.setattr(demo, "_demo_strategy", with_a_proposal)
        monkeypatch.setattr(site_discovery, "confirm", lambda _c, _o: True)
        with transaction() as session:
            store = Store(session)
            store.put_profile(store.get_profile().model_copy(update={"countries": ["GB"], "locations": ["London"]}))
        made = client.post("/strategy").json()
        assert made["markets"][0]["proposed"] == ["lawjobs.example"]
        written = next(s for s in config.sites() if s.domain == "lawjobs.example")
        assert written.terms_reviewed_on is None and written.countries == ["GB"]
        config.sites_file.cache_clear()
        config.sites.cache_clear()


@pytest.fixture(autouse=True)
def _real_sites_untouched():
    """None of these may write the repository's own sites.yaml."""
    # The path, captured now: `CONFIG_DIR` may still be monkeypatched when this tears down.
    real = config.CONFIG_DIR / "sites.yaml"
    before = real.read_text()
    yield
    assert real.read_text() == before


class TestAPlanThatCannotBeHad:
    """16–17 Nov: at five countries every plan was cut off twice, and every search asked again —
    37–115 seconds each before its first query, then the fallback queries anyway."""

    def test_its_ceiling_grows_with_the_countries(self):
        floor = config.wera().task("strategist.plan").max_tokens
        assert strategist.plan_ceiling(1) == floor
        assert strategist.plan_ceiling(10) > strategist.plan_ceiling(2) >= floor
        assert strategist.plan_ceiling(500) == strategist.PLAN_TOKENS_MAX

    def test_it_is_thought_about_without_thinking(self):
        assert config.wera().task("strategist.plan").reasoning == "off"

    def test_a_failure_is_not_asked_for_again_until_something_changes(self, client, monkeypatch):
        from wera_agent.errors import ApiError

        asked = []

        def fails(self):
            asked.append(1)
            raise ApiError(424, "LLM_TRUNCATED", "cut off")

        monkeypatch.setattr(Store, "strategy", lambda self, refusing=(): None)
        monkeypatch.setattr(Store, "refresh_strategy", fails)
        for _ in range(3):
            with transaction() as session:
                planned, matrix = Store(session).search_queries(refresh=True, probe=False)
                assert planned and matrix is None  # still searches, in the person's own words
        assert len(asked) == 1

        with transaction() as session:  # a new profile is a new plan, and is asked
            store = Store(session)
            store.put_profile(store.get_profile().model_copy(update={"titles": ["Data Engineer"]}))
        with transaction() as session:
            Store(session).search_queries(refresh=True, probe=False)
        assert len(asked) == 2

    def test_a_failure_older_than_the_window_is_asked_again(self, client, monkeypatch):
        from wera_agent.errors import ApiError
        from wera_agent.services import store as store_module

        asked = []
        monkeypatch.setattr(Store, "strategy", lambda self, refusing=(): None)
        monkeypatch.setattr(
            Store, "refresh_strategy", lambda self: asked.append(1) or (_ for _ in ()).throw(ApiError(424, "X", "x"))
        )
        now = 1_762_850_000_000
        with transaction() as session:
            Store(session, now).search_queries(refresh=True, probe=False)
        with transaction() as session:
            Store(session, now + store_module.PLAN_RETRY_MS + 1).search_queries(refresh=True, probe=False)
        assert len(asked) == 2
