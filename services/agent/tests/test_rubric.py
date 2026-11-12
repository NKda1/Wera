"""The parsing rubric: the job-description analysis and the candidate CV profile.

What these hold the rubric to is its four rules, each enforced in code rather than asked for:
terms are the post's own, metrics are numbers that are really there, a field that can't be read
off the text is null rather than invented, and soft skills never count as keywords.
"""

import pytest

from wera_agent.db.session import transaction
from wera_agent.domain.cv_profile import build_profile, metrics_in
from wera_agent.domain.verbatim import appears_in
from wera_agent.errors import ApiError
from wera_agent.llm import demo
from wera_agent.nodes.analyse import keyword_matrix
from wera_agent.schemas.jobs import FitEvidence, JobKeyword, SubScore
from wera_agent.schemas.llm_outputs import CvProfileOutput, JobAnalysisOutput, KeywordList
from wera_agent.services.store import Store

POST = """## Acme Claims
Acme Claims is expanding its fraud platform into three new EU markets this year.

## The role
- Own the **ingestion pipeline** that scores every incoming claim
- Work with the risk team on model monitoring
"""


def _sub() -> SubScore:
    return SubScore(score=5, job_quote="", cv_bullet_ids=[])


def _evidence(**kw) -> FitEvidence:
    return FitEvidence(
        core=_sub(),
        adjacent=_sub(),
        experience=_sub(),
        eligibility=_sub(),
        reason="",
        job_quote="",
        cv_bullet_ids=[],
        model="m",
        **kw,
    )


class TestVerbatim:
    def test_markdown_spacing_and_case_dont_stop_a_real_quote(self):
        assert appears_in("Own the ingestion pipeline that scores every incoming claim", POST)

    def test_a_paraphrase_is_not_a_quote(self):
        assert not appears_in("Lead the data pipeline for claims", POST)

    def test_a_trivial_span_is_not_evidence_of_anything(self):
        assert not appears_in("a", POST)
        assert not appears_in(None, POST)


class TestStrategicContext:
    """Every strategic field is a quote or null. A summary would read better and couldn't be
    checked, and the letter is written from these."""

    def _analysis(self, **context) -> JobAnalysisOutput:
        raw = {
            "roleProfile": {"department": None, "industryName": "insurance", "experienceLevel": None},
            "strategicContext": {
                "primaryJobRequirement": None,
                "companyInitiativeOrMarket": None,
                "companyProblemToSolve": None,
                "upcomingCompanyGoal": None,
                "companyValueOrPillar": None,
                **context,
            },
        }
        return JobAnalysisOutput.model_validate(raw, context={"post": POST})

    def test_a_quote_from_the_post_is_kept(self):
        out = self._analysis(companyInitiativeOrMarket="expanding its fraud platform into three new EU markets")
        assert out.strategic_context.company_initiative_or_market == "expanding its fraud platform into three new EU markets"

    def test_an_invented_field_becomes_null(self):
        """The rubric's rule 3, as a property: a model that fills `company_problem_to_solve` with
        something plausible the post never said gets null back, not its guess."""
        out = self._analysis(companyProblemToSolve="Legacy batch jobs are slowing down claim decisions")
        assert out.strategic_context.company_problem_to_solve is None


class TestKeywordMatrix:
    def test_soft_skills_go_to_culture_and_never_to_keywords(self, client):
        """Coverage, the skills line and the citation guard all read `keywords`. None of them
        should ever see "collaboration" — it once made coverage read 7%."""
        from wera_agent.nodes.fit import keywords_for

        with transaction() as session:
            store = Store(session)
            job = store._job(store._job_row("job-fernhill-mle"))
            keywords, culture = keywords_for(session, job, store.cv())
        assert keywords and all(k.category in ("hard_skill", "methodology", "tool") for k in keywords)
        assert "good docs" in culture
        assert not {c.casefold() for c in culture} & {k.term.casefold() for k in keywords}

    def test_extraction_caps_each_bucket_separately(self):
        raw = {"keywords": [{"term": f"T{i}", "weight": "nice", "category": "tool"} for i in range(34)]}
        with pytest.raises(Exception, match="At most 33"):
            KeywordList.model_validate(raw)

    def test_the_matrix_groups_keywords_by_category(self):
        evidence = _evidence(
            keywords=[
                JobKeyword(term="Python", weight="required", category="hard_skill"),
                JobKeyword(term="Scrum", weight="nice", category="methodology"),
                JobKeyword(term="Docker", weight="nice", category="tool"),
                JobKeyword(term="SQL", weight="nice"),  # stored before categories existed
            ],
            culture=["ownership"],
        )
        matrix = keyword_matrix(evidence)
        assert matrix.hard_skills == ["Python", "SQL"]
        assert matrix.methodologies_and_frameworks == ["Scrum"]
        assert matrix.tools_and_software == ["Docker"]
        assert matrix.soft_skills_and_culture == ["ownership"]


class TestThePackCarriesTheAnalysis:
    def test_a_drafted_pack_has_the_rubric_filled_from_its_post(self, client):
        """The demo seed drafts its ready packs through `drafting.draft_pack`, the real path."""
        app = next(a for a in client.get("/applications").json()["items"] if a["status"] == "ready_for_review")
        full = client.get(f"/applications/{app['id']}").json()
        analysis = full["pack"]["analysis"]
        with transaction() as session:
            store = Store(session)
            post = store._job(store._job_row(full["jobId"])).description_md or ""
        assert analysis["roleProfile"]["companyName"] == full["job"]["company"]
        for quote in analysis["strategicContext"].values():
            assert quote is None or appears_in(quote, post)
        assert any(analysis["strategicContext"].values()), "the demo should find at least one real excerpt"

    def test_a_failed_analysis_does_not_cost_the_pack(self, client, monkeypatch):
        """The analysis is context for the letter. A pack without it is the pack WERA wrote before
        the rubric existed; failing the CV over it would be a bad trade."""
        from wera_agent.services import drafting

        real = demo.respond

        def no_analysis(task, variables, context, demo_input=None):
            if task == "job.analyse":
                raise ApiError(502, "LLM_BAD_OUTPUT", "no")
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", no_analysis)
        with transaction() as session:
            store = Store(session)
            from wera_agent.schemas.api import DecisionIn

            store.decide("job-tessellate-aie", DecisionIn(action="approve"))
            app_id = store.started_application_id
        with transaction() as session:
            drafting.draft_pack(session, app_id, ready=True)
        with transaction() as session:
            pack = Store(session)._load_pack(app_id)
        assert pack is not None and pack.analysis is None


class TestMetricIsolation:
    """Rule 2, by regex: a number is in the line or it isn't, and a model is never asked."""

    @pytest.mark.parametrize(
        ("line", "found"),
        [
            ("Cut p95 latency by 40% across 3 services", ["40%", "3 services"]),
            ("Grew ARR to £1.2m in 2024", ["£1.2m"]),
            ("Led a team of 6 engineers", ["6 engineers"]),
            ("Migrated to Python 3.11, cutting cold starts by 30%", ["30%"]),
            ("Fine-tuned GPT-4 and ran it on S3", []),
            ("Joined in 2023 as the first hire", []),
        ],
    )
    def test_only_results_count(self, line, found):
        assert metrics_in(line) == found


class TestCvProfile:
    def test_the_computed_half_reads_the_cv_as_it_is(self, demo_cv):
        profile = build_profile(demo_cv, None)
        assert profile.identity.applicant_name == demo_cv.person.name
        assert profile.identity.applicant_city == "London"
        assert profile.identity.applicant_portfolio_url == "https://jordan-ellery.example"
        assert profile.core_competencies.technical_stack == [s.name for s in demo_cv.skills]
        gateway = next(r for r in profile.professional_history if r.role_id == demo_cv.experience[0].id)
        assert "FastAPI" in gateway.utilized_tools_and_frameworks
        assert profile.judged is False

    def test_a_line_with_a_number_becomes_an_achievement(self, demo_cv):
        cv = demo_cv.model_copy(deep=True)
        cv.experience[0].bullets[0].text = "Cut prompt-scan latency by 40% for 12 teams"
        role = build_profile(cv, None).professional_history[0]
        assert role.quantifiable_achievements[0].metrics == ["40%", "12 teams"]

    def test_the_judged_half_keeps_only_what_the_cv_backs(self, demo_cv):
        bullet = demo_cv.experience[0].bullets[0]
        raw = {
            "primaryDomains": ["AI tooling"],
            "methodologies": ["Prompt engineering", "Six Sigma"],
            "impliedMetrics": [
                {"bulletId": bullet.id, "phrase": "scans prompts for personal data"},
                {"bulletId": bullet.id, "phrase": "halved the error rate"},
                {"bulletId": bullet.id, "phrase": bullet.text},  # the whole line names no result
            ],
            "strongestCrossFunctionalTrait": {"trait": "Works across teams", "bulletIds": ["not-a-line"]},
        }
        out = CvProfileOutput.model_validate(raw, context={"cv": demo_cv})
        assert out.methodologies == ["Prompt engineering"]
        assert [m.phrase for m in out.implied_metrics] == ["scans prompts for personal data"]
        assert out.strongest_cross_functional_trait is None

    def test_the_profile_route_pays_once_per_cv_version(self, client):
        first = client.get("/cv/profile")
        assert first.status_code == 200
        body = first.json()
        assert body["judged"] is True
        assert "AI tooling" in body["coreCompetencies"]["primaryDomains"]
        trait = body["potentialNarrativeBridges"]["strongestCrossFunctionalTrait"]
        assert trait and trait["bulletIds"]
        assert "applicantEmail" not in body["identity"]  # CV-06: contact details stay out

        from sqlalchemy import func, select

        from wera_agent.db.models import LlmCallRow

        def profile_calls() -> int:
            with transaction() as session:
                return session.scalar(select(func.count()).select_from(LlmCallRow).where(LlmCallRow.task == "cv.profile")) or 0

        before = profile_calls()
        assert client.get("/cv/profile").json() == body
        assert profile_calls() == before
