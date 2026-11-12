"""The Strategist: a target matrix per country, and the board search that uses it.

What these hold it to: it plans only for the person's countries and ranks only boards WERA knows
in markets they serve; its search terms can't smuggle query syntax in; a board it suggests must
answer over the network and then waits for review like any other (SRCH-04); and a matrix is used
only while the CV, profile and model it was built from still hold. How the plan becomes searches
is `test_discovery.py`.
"""

import shutil
from datetime import date

import pytest

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
        assert strategist.target_countries(profile.model_copy(update={"countries": [], "locations": ["Austin, TX"]})) == []
        assert strategist.target_countries(profile.model_copy(update={"countries": ["NL"]})) == ["NL"]

    def test_a_refresh_is_kept_until_the_profile_changes(self, client):
        with transaction() as session:
            store = Store(session)
            store.put_profile(store.get_profile().model_copy(update={"countries": ["GB", "DE"]}))
        made = client.post("/strategy")
        assert made.status_code == 200, made.text
        assert {m["country"] for m in made.json()["markets"]} == {"GB", "DE"}
        assert client.get("/strategy").json() == made.json()

        with transaction() as session:
            store = Store(session)
            store.put_profile(store.get_profile().model_copy(update={"countries": ["GB"]}))
        assert client.get("/strategy").json() is None  # a plan for a search nobody is running now

    def test_verified_proposals_are_appended_unreviewed(self, client, tmp_path, monkeypatch):
        shutil.copy(config.CONFIG_DIR / "sites.yaml", tmp_path / "sites.yaml")
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
            store.put_profile(store.get_profile().model_copy(update={"countries": ["GB"]}))
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
