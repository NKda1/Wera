"""CV-08 and MATCH-04, 18 Nov: what a CV edit does to the scores, and what it never does.

On 18 Nov a skill-level edit on the master CV cleared every job, score and pack (the reset ran on
every save), and the daily scoring limit then counted the deleted scores, so nothing could be
scored again until the next day. An edit now keeps everything; a score is stale only when what the
scorer reads changed; the review list is re-scored first and the board is offered.
"""

import pytest
from sqlalchemy import select

from wera_agent.clock import iso
from wera_agent.db.models import FitReportRow, IntakeRunRow, LlmCallRow
from wera_agent.db.session import transaction
from wera_agent.errors import ApiError
from wera_agent.graphs import intake
from wera_agent.llm.prompts import cv_text
from wera_agent.nodes import fit
from wera_agent.services import store as store_module
from wera_agent.services.store import Store


@pytest.fixture
def scored(client):
    """Every job on the board and on the review list scored against today's CV, with its
    fingerprint — the state a CV edit starts from."""
    for scope in ("board", "review"):
        intake.judge([], now=0, run_ids={}, scope=scope, limit=500)
    with transaction() as session:
        store = Store(session)
        assert not store.jobs_needing_score(cv_version=store.cv().version, model=store.scoring_model())


def rescore_runs() -> list[IntakeRunRow]:
    with transaction() as session:
        return list(session.scalars(select(IntakeRunRow).where(IntakeRunRow.channel == "rescore")).all())


class TestWhatTheScorerReads:
    def test_skill_levels_reach_the_scorer_and_an_unset_one_reads_proficient(self, store):
        cv = store.cv()
        cv.skills[0].level, cv.skills[1].level = "familiar", None
        text = cv_text(cv, home=False)
        assert f"{cv.skills[0].name} (familiar)" in text
        assert f"{cv.skills[1].name} (proficient)" in text

    def test_a_skill_the_model_copies_with_its_level_is_still_the_cvs_skill(self, store):
        """The level is in the CV text every prompt reads, and a model copies it into the skills
        it picks. The pack eval on 18 Nov came back with no skills line at all because of it."""
        from wera_agent.schemas.llm_outputs import ProfileProposal, TailorSelection

        cv = store.cv()
        name = cv.skills[0].name
        picked = TailorSelection.model_validate(
            {
                "summaryId": None,
                "experience": [],
                "projects": [],
                "skills": [f"{name} (proficient)", "Cobol (expert)"],
                "omittedBulletIds": [],
            },
            context={"cv": cv},
        )
        assert picked.skills == [name]
        proposal = ProfileProposal.model_validate(
            {"titles": ["Engineer"], "skills": [f"{name} (Familiar)"], "seniority": ["graduate"], "exclude": []},
            context={"cv": cv},
        )
        assert proposal.skills == [name]

    def test_the_fingerprint_moves_with_a_level_and_not_with_an_alias(self, store):
        cv = store.cv()
        before = fit.scoring_fingerprint(cv)
        cv.skills[0].aliases = ["py3"]
        assert fit.scoring_fingerprint(cv) == before  # the scorer never reads aliases
        cv.skills[0].level = "expert"
        assert fit.scoring_fingerprint(cv) != before


class TestAnEdit:
    def test_an_edit_the_scorer_cannot_see_keeps_every_score_and_starts_nothing(self, scored):
        with transaction() as session:
            store = Store(session)
            cv = store.cv()
            cv.skills[0].aliases = ["something else"]
            store.put_cv(cv)
        assert rescore_runs() == []
        with transaction() as session:
            store = Store(session)
            assert not store.jobs_needing_score(cv_version=store.cv().version, model=store.scoring_model())
            assert not any(j.fit.stale for j in store.list_jobs("new"))

    def test_a_level_edit_re_scores_the_review_list_first_and_offers_the_board(self, scored):
        with transaction() as session:
            store = Store(session)
            cv = store.cv()
            cv.skills[0].level = "expert" if cv.skills[0].level != "expert" else "familiar"
            store.put_cv(cv)
            review = store.jobs_needing_score(cv_version=store.cv().version, model=store.scoring_model(), scope="review")
            assert review, "the demo seed's applications should need a new score"
        (run,) = rescore_runs()

        intake.rescore(run.id, scope="review")

        with transaction() as session:
            store = Store(session)
            version, model = store.cv().version, store.scoring_model()
            assert store.jobs_needing_score(cv_version=version, model=model, scope="review") == []
            # The board is offered, not re-scored unasked.
            assert store.stale_on_board() > 0
            assert store.shortlist_meta().stale_scores == store.stale_on_board()

    def test_the_board_offer_re_scores_the_rest(self, scored):
        with transaction() as session:
            store = Store(session)
            cv = store.cv()
            cv.skills[0].level = "familiar" if cv.skills[0].level != "familiar" else "expert"
            store.put_cv(cv, rescore=False)
            ids = store.start_rescore("board")
        intake.rescore(ids.run_ids[0], scope="board")
        with transaction() as session:
            assert Store(session).stale_on_board() == 0

    def test_nothing_stale_means_the_offer_says_so(self, scored):
        with transaction() as session, pytest.raises(ApiError) as refused:
            Store(session).start_rescore("board")
        assert refused.value.code == "NOTHING_TO_SCORE"

    def test_a_new_score_carries_the_fingerprint(self, scored):
        with transaction() as session:
            store = Store(session)
            hashes = set(session.scalars(select(FitReportRow.cv_hash)).all())
            assert store.cv_fingerprint() in hashes


class TestStartingFresh:
    def test_starting_fresh_needs_clearing_allowed(self, api, monkeypatch):
        pinned = store_module.wera().model_copy(update={"allow_clear_jobs": False})
        monkeypatch.setattr(store_module, "wera", lambda: pinned)
        cv = api.ok("GET", "/cv")
        assert api.code("PUT", "/cv?replace=true&fresh=true", cv) == "CLEAR_JOBS_OFF"

    def test_the_daily_limit_counts_from_a_reset(self, client):
        """The scores those calls bought are gone; counting them left a cleared list unscorable
        until the next day. The calls stay in the ledger for the weekly budget."""
        with transaction() as session:
            store = Store(session)
            for _ in range(3):
                session.add(
                    LlmCallRow(
                        task="fit.score",
                        tier="scoring",
                        model="m",
                        input_tokens=1,
                        output_tokens=1,
                        cost_usd=0.01,
                        latency_ms=1,
                        ok=True,
                        attempt=1,
                        called_at=iso(store.now - 1000),
                    )
                )
        with transaction() as session:
            assert Store(session).scored_today() >= 3
            Store(session).reset_job_world()
        with transaction() as session:
            assert Store(session).scored_today() == 0
            assert len(session.scalars(select(LlmCallRow)).all()) >= 3  # still in the ledger
