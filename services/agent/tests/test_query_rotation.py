"""SRCH-20 (21 Nov): a search's other phrasings, and the rotation that sends a new one each search.

Every search used to send the same plan, and Google ranks a query the same way all day, so a
second search the same day found its postings "already saved". Each planned search now carries
other ways to ask for the same job (`strategist.queries`), kept to the person's level, exclusions
and role family, and what a pool asked is remembered in Redis (`sources.rotation`): a search asked
in the last three days goes on to its next page if that page found postings, else to its next
phrasing — one query per planned search, in the plan's order, inside the same Serper cap.
"""

from datetime import date

import fakeredis
import pytest

from wera_agent.config import RotationConfig, Site
from wera_agent.nodes import strategist
from wera_agent.schemas.strategy import LocalTerm, Market, PlannedQuery, TargetMatrix
from wera_agent.sources import rotation
from wera_agent.sources.provider_guard import Guard

BOARD = Site(domain="builtinlondon.uk", name="Built In London", terms_reviewed_on="2026-01-01", countries=["GB"])
NOW = 1_763_700_000_000
DAY = rotation.DAY_MS
TODAY = date(2026, 11, 21)


def profile(store, **over):
    base = {
        "titles": ["Graduate Software Engineer", "Junior Software Engineer", "Backend Engineer"],
        "countries": ["GB"],
        "locations": ["London"],
        "city_scope": "only",
        "remote": "any",
        "seniority": ["graduate", "junior"],
        "sites": [BOARD.domain],
        "exclude": ["Senior", "Lead"],
    }
    return store.get_profile().model_copy(update=base | over)


def matrix(field: str, terms: list[LocalTerm] | None = None, country: str = "GB") -> TargetMatrix:
    return TargetMatrix(
        field=field,
        markets=[Market(country=country, languages=["en"], search_terms=terms or [], boards=[], proposed=[])],
        cv_version=1,
        profile_key="k",
        model="m",
        generated_at="t",
    )


class Stack:
    """The one part of a CV profile `queries` reads: the CV's own skills, in its order."""

    def __init__(self, *skills: str):
        self.core_competencies = type("C", (), {"technical_stack": list(skills)})()


def plan(store, cvp=None, m=None, **over) -> list[PlannedQuery]:
    return strategist.queries(profile(store, **over), cvp, [BOARD], m, today=TODAY)


class TestThePhrasings:
    def test_each_search_carries_other_ways_to_ask_for_the_job(self, store):
        planned = plan(store, Stack("Python", "Go"), matrix("backend engineering"))
        first = next(q for q in planned if q.tier == 1)
        assert first.q == '"Graduate Software Engineer" job in "London" -site:linkedin.com'
        assert first.variants[:3] == [
            'graduate "Software Engineer" jobs in "London" -site:linkedin.com',
            'graduate "Software Engineer" Python jobs in "London" -site:linkedin.com',
            '"Software Engineer" graduate scheme 2027 -site:linkedin.com',
        ]
        assert 'graduate "backend engineering" jobs in "London" -site:linkedin.com' in first.variants
        assert all(len(q.variants) <= RotationConfig().variants for q in planned)

    def test_a_title_with_a_level_asks_in_its_own_level_first(self, store):
        planned = plan(store)
        junior = next(q for q in planned if q.tier == 1 and q.title == "Junior Software Engineer")
        assert junior.variants[0] == 'junior "Software Engineer" "London" -site:linkedin.com'

    def test_only_the_persons_own_levels_are_asked(self, store):
        grad = {v for q in plan(store, seniority=["graduate"]) for v in q.variants}
        assert any(v.startswith("graduate ") for v in grad)
        assert not any("junior" in v or "entry level" in v or "internship" in v for v in grad)
        interns = {v for q in plan(store, seniority=["intern"]) for v in q.variants}
        assert any("internship 2027" in v for v in interns)

    def test_someone_open_to_mid_level_gets_no_level_words(self, store):
        planned = plan(store, Stack("Python"), seniority=["junior", "mid"])
        words = " ".join(v for q in planned for v in q.variants).lower()
        assert words and not any(w in words.split() for w in ("graduate", "junior", "entry", "intern"))
        assert '"Backend Engineer" hiring "London" -site:linkedin.com' in {v for q in planned for v in q.variants}

    def test_a_phrasing_with_an_excluded_word_is_never_asked(self, store):
        planned = plan(store, Stack("Git", "Python"), exclude=["Senior", "Git"])
        variants = [v for q in planned for v in q.variants]
        assert any("Python" in v for v in variants) and not any("Git" in v for v in variants)

    def test_a_finance_persona_grows_no_quant_phrasings(self, store):
        """UPSK-07: quant roles sit between finance and software and ask for both."""
        finance = ["Investment Banking Analyst", "Corporate Finance Analyst"]
        m = matrix("quantitative finance and investment banking")
        variants = [v for q in plan(store, Stack("Derivatives Pricing", "Excel"), m, titles=finance) for v in q.variants]
        assert any('"investment banking"' in v for v in variants)
        assert not any("quantitative finance" in v or "Derivatives Pricing" in v for v in variants)
        quant = ["Quantitative Analyst", "Quant Researcher"]
        kept = [v for q in plan(store, Stack("Derivatives Pricing"), m, titles=quant) for v in q.variants]
        assert any('"quantitative finance"' in v for v in kept) and any("Derivatives Pricing" in v for v in kept)

    def test_no_phrasing_repeats_a_search_or_another_phrasing(self, store):
        planned = plan(store, Stack("Python", "Go"), matrix("software engineering"))
        every = [(q.q, q.gl, q.hl) for q in planned if q.tier != 4] + [
            (v, q.gl, q.hl) for q in planned if q.tier != 4 for v in q.variants
        ]
        assert len(every) == len(set(every))
        # Three titles that are one role at three levels share its phrasings out: none is left bare.
        assert all(q.variants for q in planned if q.tier == 1)

    def test_a_board_search_keeps_its_site_and_an_open_one_never_asks_linkedin(self, store):
        planned = plan(store)
        assert all(v.startswith("site:builtinlondon.uk ") for q in planned if q.source == "board" for v in q.variants)
        assert all(v.endswith("-site:linkedin.com") for q in planned if q.source == "open" for v in q.variants)

    def test_a_local_language_term_gets_no_english_words(self, store):
        m = matrix("software", [LocalTerm(term="Softwareentwickler", language="de", title="Graduate Software Engineer")], "DE")
        planned = strategist.queries(profile(store, countries=["DE"], locations=["Berlin"]), None, [BOARD], m, today=TODAY)
        assert not [q for q in planned if q.hl == "de" and q.variants]

    def test_graduate_schemes_are_asked_for_next_years_intake_from_august(self):
        assert strategist._scheme_year(date(2026, 11, 21)) == 2027
        assert strategist._scheme_year(date(2027, 3, 1)) == 2027


def q(text: str, *, tier: int = 1, variants: list[str] | None = None, page: int = 1) -> PlannedQuery:
    return PlannedQuery(q=text, gl="gb", hl="en", source="open", tier=tier, title="T", page=page, variants=variants or [])


def remember(*sent: tuple[str, int, int], at: int = NOW) -> dict[str, rotation.Record]:
    """(words, page, postings) → the memory a search would have left."""
    return {rotation.asked_field("gb", "en", text, page): rotation.Record(at=at, postings=n) for text, page, n in sent}


class TestTheRotation:
    def test_with_nothing_asked_the_plan_goes_as_planned(self):
        planned = [q("a", variants=["a2"]), q("b", tier=2)]
        turned = rotation.rotate(planned, {}, now=NOW)
        assert turned.plan == planned and turned.as_planned == 2

    def test_a_search_that_found_postings_moves_to_its_next_page(self):
        turned = rotation.rotate([q("a", variants=["a2"])], remember(("a", 1, 1)), now=NOW)
        assert [(x.q, x.page) for x in turned.plan] == [("a", 2)] and turned.deeper == 1

    def test_one_that_found_none_moves_to_its_next_phrasing(self):
        turned = rotation.rotate([q("a", variants=["a2", "a3"])], remember(("a", 1, 0)), now=NOW)
        assert [(x.q, x.page) for x in turned.plan] == [("a2", 1)] and turned.rephrased == 1

    def test_pages_go_to_three_then_the_next_phrasing(self):
        memory = remember(("a", 1, 4), ("a", 2, 3), ("a", 3, 2))
        assert [(x.q, x.page) for x in rotation.rotate([q("a", variants=["a2"])], memory, now=NOW).plan] == [("a2", 1)]

    def test_after_three_days_the_plans_own_words_come_back(self):
        memory = remember(("a", 1, 0), at=NOW - 3 * DAY - 1)
        assert rotation.rotate([q("a", variants=["a2"])], memory, now=NOW).plan[0].q == "a"

    def test_a_search_with_nothing_new_rests_and_the_tier_order_is_kept(self):
        planned = [q("a"), q("b", variants=["b2"]), q("c", tier=2)]
        turned = rotation.rotate(planned, remember(("a", 1, 0), ("b", 1, 0)), now=NOW)
        assert [(x.q, x.tier) for x in turned.plan] == [("b2", 1), ("c", 2)] and turned.rested == 1

    def test_tier_four_follows_what_tier_one_sent(self):
        planned = [q("a", variants=["a2"]), q("a", tier=4, page=2, variants=["a2"])]
        assert [(x.q, x.page) for x in rotation.rotate(planned, remember(("a", 1, 2)), now=NOW).plan] == [("a", 2), ("a", 3)]
        assert [(x.q, x.page) for x in rotation.rotate(planned, remember(("a", 1, 0)), now=NOW).plan] == [("a2", 1), ("a2", 2)]
        spent = remember(("a", 1, 5), ("a", 2, 5))
        assert [(x.q, x.page) for x in rotation.rotate(planned, spent, now=NOW).plan] == [("a", 3)]  # no page 4

    def test_exclusions_added_after_planning_go_with_any_phrasing_and_dont_make_it_new(self):
        planned = [
            q(
                '"x" job -site:linkedin.com -site:indeed.com -intitle:"Security"',
                variants=['graduate "x" jobs -site:linkedin.com'],
            )
        ]
        memory = remember(('"x" job -site:linkedin.com', 1, 0))  # asked before indeed.com refused
        sent = rotation.rotate(planned, memory, now=NOW).plan[0]
        assert sent.q == 'graduate "x" jobs -site:linkedin.com -site:indeed.com -intitle:"Security"'


class TestTheMemory:
    def test_what_was_sent_is_remembered_for_a_week_per_pool(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        asked = rotation.Asked(redis, "software-engineering:graduate", now=NOW)
        asked.note(q("a -site:indeed.com"), 3)
        assert redis.ttl("wera:asked:software-engineering:graduate") == 7 * 24 * 60 * 60
        assert asked.recall() == {rotation.asked_field("gb", "en", "a", 1): rotation.Record(at=NOW, postings=3)}
        assert rotation.Asked(redis, "other-pool", now=NOW).recall() == {}

    def test_older_than_the_memory_is_forgotten(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        rotation.Asked(redis, "p", now=NOW - 8 * DAY).note(q("a"), 1)
        rotation.Asked(redis, "p", now=NOW).note(q("b"), 1)
        assert list(rotation.Asked(redis, "p", now=NOW).recall()) == [rotation.asked_field("gb", "en", "b", 1)]
        assert redis.hlen("wera:asked:p") == 1

    def test_without_redis_nothing_is_remembered_and_nothing_fails(self):
        class Down:
            def __getattr__(self, _name):
                raise ConnectionError("down")

        for client in (None, Down()):
            asked = rotation.Asked(client, "p", now=NOW)
            asked.note(q("a"), 1)
            assert asked.recall() == {}


class TestPageDepth:
    def test_tier_four_after_a_rotated_page_two_is_judged_by_page_two(self):
        planned = [q("a", page=2), q("a", tier=4, page=3)]
        rich = {strategist.page_key("a", 2): 6}
        assert [x.page for x in strategist.page_two_worth_asking(planned, rich)] == [2, 3]
        assert [x.page for x in strategist.page_two_worth_asking(planned, {"a": 6})] == [2]  # page 1's count is not page 2's


class TestWhatIsLeft:
    def test_the_share_a_search_may_use_and_what_is_left_of_it(self):
        redis = fakeredis.FakeRedis(decode_responses=True)
        guard = Guard(redis, "serper")
        share = int(guard.daily * guard.config.stop_at)
        assert guard.left_today() == (share, share)
        guard.count(7)
        assert guard.left_today() == (share - 7, share)
        guard.count(share)
        assert guard.left_today() == (0, share)

    def test_a_provider_with_no_daily_limit_says_nothing(self):
        assert Guard(fakeredis.FakeRedis(), "reliefweb").left_today() is None


@pytest.mark.parametrize("field_", ["investment banking and corporate finance", "Investment banking, corporate finance"])
def test_a_field_names_its_parts_one_at_a_time(field_):
    assert strategist._field_parts(matrix(field_)) == ["investment banking", "corporate finance"]
