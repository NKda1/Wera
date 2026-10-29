"""The golden eval (SPEC §12.2, ARCHITECTURE §11).

Two things are worth testing here, and they are different: that the *comparison* is right (an
agreement figure nobody can trust is worse than none), and that the harness runs the whole
golden set through the real scoring path without touching the real database.
"""

import json

import pytest
import yaml

from wera_agent import evaluate
from wera_agent.evaluate import Report, Row
from wera_agent.paths import FIXTURES_DIR
from wera_agent.settings import get_settings


def row(hand=None, computed=None, **kw) -> Row:
    return Row(
        id=kw.pop("id", "gold-x"),
        title=kw.pop("title", "A role"),
        company=kw.pop("company", "A company"),
        hand=hand,
        computed=computed,
        rough_only=kw.pop("rough_only", False),
        keywords_expected=kw.pop("keywords_expected", 0),
        **kw,
    )


def report(rows: list[Row], *, demo_mode: bool = False) -> Report:
    return Report(
        model="test/model",
        demo_mode=demo_mode,
        cv_version=1,
        cv_source="test",
        ran_at="2026-10-20T00:00:00.000Z",
        rows=rows,
    )


class TestAgreement:
    """±1.5 is the tolerance SPEC §12.2 names, so the boundary itself has to be right."""

    @pytest.mark.parametrize(
        ("hand", "computed", "agrees"),
        [
            (8.0, 8.0, True),
            (8.0, 9.5, True),  # exactly 1.5 out still agrees
            (8.0, 6.5, True),
            (8.0, 9.6, False),
            (8.0, 6.4, False),
        ],
    )
    def test_the_tolerance_boundary(self, hand, computed, agrees):
        assert row(hand, computed).agrees is agrees

    def test_an_unscored_ad_is_not_counted_either_way(self):
        r = report([row(8.0, 8.0), row(None, 7.0), row(None, 2.0)])
        assert len(r.compared) == 1
        assert r.agreement == 100.0
        assert len(r.unscored) == 2

    def test_no_hand_scores_means_no_agreement_figure_rather_than_zero(self):
        r = report([row(None, 7.0), row(None, 2.0)])
        assert r.agreement is None
        assert r.mean_error is None
        assert r.keyword_recall is None

    def test_agreement_is_a_share_of_what_was_compared(self):
        r = report([row(8.0, 8.0), row(8.0, 8.0), row(8.0, 8.0), row(8.0, 2.0)])
        assert r.agreement == 75.0


class TestBias:
    """A model that reads two points high ranks jobs fine and breaks every threshold set by eye,
    so the signed error is reported next to the absolute one."""

    def test_a_consistently_generous_model_shows_a_positive_bias(self):
        r = report([row(5.0, 7.0), row(6.0, 8.0), row(4.0, 6.0)])
        assert r.bias == pytest.approx(2.0)
        assert r.mean_error == pytest.approx(2.0)

    def test_errors_in_both_directions_cancel_in_the_bias_but_not_the_mean(self):
        r = report([row(5.0, 7.0), row(5.0, 3.0)])
        assert r.bias == pytest.approx(0.0)
        assert r.mean_error == pytest.approx(2.0)


class TestKeywordRecall:
    def test_wording_differences_still_count_as_found(self):
        # Narrower, wider, and a plural: all the same requirement.
        assert evaluate.keyword_hit("LLM guardrails", ["guardrails"])
        assert evaluate.keyword_hit("Python", ["Python 3"])
        assert evaluate.keyword_hit("REST APIs", ["REST API"])
        assert evaluate.keyword_hit("FastAPI", ["fastapi"])

    def test_a_different_requirement_is_not_a_match(self):
        assert not evaluate.keyword_hit("SQL", ["PostgreSQL"])
        assert not evaluate.keyword_hit("Python", ["TypeScript"])
        assert not evaluate.keyword_hit("Docker", [])

    def test_recall_is_the_share_of_expected_terms_found(self):
        assert evaluate.recall(["Python", "Docker"], ["Python", "Go"]) == 0.5
        assert evaluate.recall(["Python"], ["Python"]) == 1.0

    def test_expecting_nothing_is_no_measurement_not_zero_percent(self):
        """An ad with no expected keywords must not drag the average down to 0%."""
        assert evaluate.recall([], ["Python"]) is None
        r = report([row(keyword_recall=1.0), row(keyword_recall=None)])
        assert r.keyword_recall == 1.0


class TestVerdict:
    def test_an_ad_that_could_not_be_scored_fails_the_run(self):
        r = report([row(8.0, 8.0), row(7.0, None, error="LLM_TIMEOUT")])
        assert r.failed and not r.passes

    def test_live_agreement_below_eighty_percent_fails(self):
        r = report([row(8.0, 8.0), row(8.0, 2.0)])  # 50%
        assert r.agreement == 50.0
        assert not r.passes

    def test_live_agreement_at_eighty_percent_passes(self):
        r = report([*[row(8.0, 8.0)] * 4, row(8.0, 2.0)])
        assert r.agreement == 80.0
        assert r.passes

    def test_demo_mode_is_not_judged_on_agreement(self):
        """The demo responders were written to exercise the contract, not to match a person.
        Failing CI when they disagree would be noise; failing it when an ad won't score is not."""
        r = report([row(8.0, 2.0), row(8.0, 2.0)], demo_mode=True)
        assert r.agreement == 0.0
        assert r.passes
        r.rows.append(row(8.0, None, error="LLM_BAD_OUTPUT"))
        assert not r.passes


class TestTheGoldenSetItself:
    def test_every_ad_has_a_hand_score_block(self):
        """An ad with no block is silently never compared, which is the kind of gap that makes
        an eval look healthier than it is."""
        golden = evaluate.load_golden()
        hand = yaml.safe_load((evaluate.golden_dir() / "hand_scores.yaml").read_text())["scores"]
        assert {g.id for g in golden} == set(hand)

    def test_the_set_is_big_enough_and_spans_the_range(self):
        golden = evaluate.load_golden()
        assert len(golden) >= 20
        # A set of twenty good matches would agree with any model that says 8 to everything.
        assert sum(1 for g in golden if g.rough_only) >= 2, "no rough-only ads: the §8.2 cap is untested"
        assert len({g.ad["company"] for g in golden}) == len(golden)

    def test_every_ad_loads_as_a_job(self):
        for g in evaluate.load_golden():
            job = evaluate.to_job(g.ad, 1_760_000_000_000)
            assert job.id == g.id
            assert job.description_status == ("missing" if g.rough_only else "full")

    def test_a_hand_score_outside_the_scale_is_rejected(self, tmp_path, monkeypatch):
        ads = {"jobs": [{"id": "a", "title": "T", "company": "C", "city": "London", "postedDaysAgo": 1}]}
        (tmp_path / "jobs.json").write_text(json.dumps(ads))
        (tmp_path / "hand_scores.yaml").write_text("scores:\n  a:\n    total: 12\n")
        monkeypatch.setattr(evaluate, "golden_dir", lambda: tmp_path)
        with pytest.raises(ValueError, match="between 0 and 10"):
            evaluate.load_golden()

    def test_a_hand_score_for_an_ad_that_does_not_exist_is_rejected(self, tmp_path, monkeypatch):
        """A renamed ad leaves its score orphaned, and the eval would quietly stop comparing it."""
        ads = {"jobs": [{"id": "a", "title": "T", "company": "C", "city": "London", "postedDaysAgo": 1}]}
        (tmp_path / "jobs.json").write_text(json.dumps(ads))
        (tmp_path / "hand_scores.yaml").write_text("scores:\n  a: {total: 5}\n  gone: {total: 5}\n")
        monkeypatch.setattr(evaluate, "golden_dir", lambda: tmp_path)
        with pytest.raises(ValueError, match="aren't in jobs.json"):
            evaluate.load_golden()


class TestRunningIt:
    """End to end on the demo responders — the same path CI runs (DEMO-02: no network)."""

    def test_the_whole_set_scores_through_the_real_path(self):
        result = evaluate.run()
        assert len(result.rows) == len(evaluate.load_golden())
        assert not result.failed
        assert all(r.computed is not None for r in result.rows)
        assert result.demo_mode is True
        assert result.passes

    def test_an_ad_with_no_description_is_capped_at_the_rough_only_total(self):
        result = evaluate.run(only=["gold-16-rookhaven-ai-scheme"])
        (only,) = result.rows
        assert only.rough_only
        assert only.computed is not None and only.computed <= 6.5

    def test_the_eval_never_writes_to_the_configured_database(self, client):
        """A golden ad on someone's real shortlist is indistinguishable from a real vacancy."""
        from sqlalchemy import select

        from wera_agent.db.models import JobRow
        from wera_agent.db.session import transaction

        url = get_settings().db_url
        assert evaluate.run(only=["gold-01-aurelis-ai-platform"]).rows
        # The URL is restored, and nothing from the golden set reached this database.
        assert get_settings().db_url == url
        with transaction() as session:
            ids = set(session.scalars(select(JobRow.id)).all())
        assert ids and not any(i.startswith("gold-") for i in ids)

    def test_a_named_ad_that_does_not_exist_is_an_error_not_an_empty_report(self):
        with pytest.raises(ValueError, match="No golden ad"):
            evaluate.run(only=["gold-nope"])

    def test_the_cv_can_be_pinned_to_a_file(self):
        """A report has to name the CV it scored against, or it means nothing a month later."""
        path = FIXTURES_DIR / "demo" / "cv.json"
        pinned = evaluate.run(only=["gold-01-aurelis-ai-platform"], cv_path=path)
        assert pinned.cv_source == str(path)
        assert f"— {path}" in evaluate.as_markdown(pinned)
        # Saved, not just validated, so the version in the report is this run's real one.
        seeded = evaluate.run(only=["gold-01-aurelis-ai-platform"])
        assert pinned.cv_version == seeded.cv_version + 1
        assert "seeded" in seeded.cv_source

    def test_a_cv_file_that_does_not_validate_is_rejected(self, tmp_path):
        """A pinned CV goes through the same checks the CV page applies, and says which field."""
        from wera_agent.errors import ApiError

        bad = tmp_path / "cv.yaml"
        bad.write_text("person: {name: Nobody}\n")
        with pytest.raises(ApiError) as caught:
            evaluate.run(only=["gold-01-aurelis-ai-platform"], cv_path=bad)
        assert caught.value.code == "VALIDATION"
        assert "version" in caught.value.message

    def test_the_report_writes_markdown_and_json_together(self, tmp_path):
        result = evaluate.run(only=["gold-01-aurelis-ai-platform"])
        md, data = evaluate.write_report(result, tmp_path / "report.md")
        assert md.read_text().startswith("# Golden eval")
        assert json.loads(data.read_text())["ads"] == 1

    def test_a_hand_scored_ad_produces_an_agreement_figure(self, tmp_path, monkeypatch):
        """The whole chain with a real hand score in it: loaded, compared, and in the report."""
        ad = json.loads((FIXTURES_DIR / "golden" / "jobs.json").read_text())["jobs"][0]
        (tmp_path / "jobs.json").write_text(json.dumps({"jobs": [ad]}))
        (tmp_path / "hand_scores.yaml").write_text(f"scores:\n  {ad['id']}:\n    total: 8.0\n    keywords: [Python, FastAPI]\n")
        monkeypatch.setattr(evaluate, "golden_dir", lambda: tmp_path)

        result = evaluate.run()
        (only,) = result.rows
        assert only.hand == 8.0
        assert only.computed is not None
        assert result.agreement in (0.0, 100.0)
        assert result.mean_error is not None and result.bias is not None
        assert only.keyword_recall == 1.0
        text = evaluate.as_markdown(result)
        assert "8.0" in text and "Agreement within" in text

    def test_the_markdown_says_when_the_scores_came_from_the_demo_responders(self):
        """A demo report that read like a live one would be the worst possible artefact."""
        result = evaluate.run(only=["gold-01-aurelis-ai-platform"])
        assert "demo responders" in evaluate.as_markdown(result)


class TestThePackEval:
    """SCH-05 for the writing tier (`wera eval --packs`).

    The scoring tier was the only thing measured, and it is the tier that works: `fit.score`
    succeeded on 40 of 41 real calls while the writing tier managed 35 of 55. Every failure that
    reached a person came from the half nothing was watching — a draft parked as
    `changes_requested` after `cv.tailor` returned the wrong shape twice, and before that a
    tailored CV with a summary, no bullets and no skills that the guard passed.

    There is no agreement figure here on purpose. Nobody hand-scores prose, and the failures
    that matter are not matters of taste: wrong shape, nothing selected, a citation that isn't
    in the CV. Those are checkable.
    """

    def test_it_drafts_real_packs_end_to_end(self):
        result = evaluate.run_packs(ads=2)
        assert len(result.rows) == 2
        assert result.passed, result.failures
        assert all(r.bullets > 0 for r in result.rows)

    def test_a_pack_with_no_bullets_counts_as_a_failure(self):
        """The bug this exists for. A tailored CV with a summary and nothing else passes the
        guard — the guard checks that what is present is cited, never that anything is — and a
        real run shipped exactly that, which the ATS step then scored at 17%."""
        empty = evaluate.PackRow(id="gold-x", title="A role", company="A company", bullets=0, guard_pass=True)
        assert not empty.ok

    def test_one_bad_pack_does_not_end_the_run(self, monkeypatch):
        from wera_agent.services import drafting

        calls = {"n": 0}
        real = drafting.draft_pack

        def sometimes(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("provider had a moment")
            return real(*a, **kw)

        monkeypatch.setattr(drafting, "draft_pack", sometimes)
        result = evaluate.run_packs(ads=2)
        assert len(result.rows) == 2
        assert not result.passed
        assert result.failures  # and it says which one, by code

    def test_every_pack_must_come_back_whole(self):
        """No tolerance, unlike the ±1.5 on agreement: a pack that failed is one the person
        cannot send, so 'most of them worked' is not a passing grade."""
        rows = [
            evaluate.PackRow(id="a", title="t", company="c", bullets=5),
            evaluate.PackRow(id="b", title="t", company="c", bullets=0),
        ]
        report = evaluate.PackReport(model="m", cv_version=1, cv_source="x", ran_at="now", rows=rows)
        assert report.success_rate == 50.0
        assert not report.passed

    def test_the_report_names_what_went_wrong(self):
        rows = [evaluate.PackRow(id="a", title="t", company="c", error="LLM_BAD_OUTPUT")]
        report = evaluate.PackReport(model="m", cv_version=1, cv_source="x", ran_at="now", rows=rows)
        report.failures = {"LLM_BAD_OUTPUT": 1}
        assert "LLM_BAD_OUTPUT" in evaluate.packs_as_markdown(report)
        assert "FAIL" in evaluate.packs_as_markdown(report)

    def test_it_never_writes_to_the_configured_database(self, client):
        from sqlalchemy import select

        from wera_agent.db.models import ApplicationRow
        from wera_agent.db.session import transaction

        url = get_settings().db_url
        assert evaluate.run_packs(ads=1).rows
        assert get_settings().db_url == url
        with transaction() as session:
            ids = set(session.scalars(select(ApplicationRow.id)).all())
        assert not any(i.startswith("pack-gold-") for i in ids)
