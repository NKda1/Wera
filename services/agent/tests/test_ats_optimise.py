"""The CV-for-ATS optimiser (ATSC-10): on a redraft, a few bullets reworded to carry keywords the
master CV backs, each held to its cited lines, a refused one left as it was.

The model is a fake throughout: `demo.respond` is replaced for `cv.optimise`, so each test says
exactly what the "model" wrote and the checks are what is under test.
"""

import asyncio

import pytest

from wera_agent.config import wera
from wera_agent.db.session import transaction
from wera_agent.errors import ApiError
from wera_agent.graphs import application as graph
from wera_agent.llm import demo
from wera_agent.nodes import ats_optimise
from wera_agent.nodes.ats_optimise import optimise, targets
from wera_agent.schemas.api import DecisionIn, DocumentPatchIn
from wera_agent.schemas.cv import Skill
from wera_agent.schemas.jobs import JobKeyword
from wera_agent.schemas.pack import Cited, CvItem, CvSection, GuardResult, OptimiseReport, TailoredCV
from wera_agent.services import drafting
from wera_agent.services.store import Store

JOB = "job-fernhill-mle"

# The demo CV's lines this pack shows. Left out: exp-idsu-02 (Azure, C# / .NET), exp-fm-02
# (Stripe, Postmark), exp-fm-03 (a LangGraph agent in Python) and the open-source project.
SHOWN = {"exp-idsu": ["exp-idsu-01", "exp-idsu-03"], "exp-fm": ["exp-fm-01"]}
# Nice-to-have, the two backed ones: a *required* keyword in a left-out line is brought in whole,
# in code, before any rewording (`place_required`), and so is never the optimiser's to place.
KEYWORDS = [
    JobKeyword(term="Azure", weight="nice"),  # in a left-out line of a shown role
    JobKeyword(term="Kubernetes", weight="required"),  # nowhere in the CV
    JobKeyword(term="FastAPI", weight="required"),  # already in a shown line
    # A skill, in a left-out line of a shown role, and on the CV in its skills line: a nice one
    # there doesn't move "Shown in your work" while a required one is matched, so not a target.
    JobKeyword(term="Python", weight="nice"),
    JobKeyword(term="Postmark", weight="nice"),  # in a left-out line, not a skill
]


def doc_for(cv) -> TailoredCV:
    text = {b.id: b.text for r in [*cv.experience, *cv.projects] for b in r.bullets}
    return TailoredCV(
        job_id=JOB,
        cv_version=cv.version,
        summary=Cited(text=cv.summary.text, source_ids=[cv.summary.id]),
        sections=[
            CvSection(
                kind="experience",
                items=[
                    CvItem(role_id=role, bullets=[Cited(text=text[i], source_ids=[i]) for i in ids])
                    for role, ids in SHOWN.items()
                ],
            )
        ],
        skills=["FastAPI"],
        omitted_bullet_ids=[],
        guard=GuardResult(pass_=True, violations=[], repaired=False),
    )


@pytest.fixture
def pack(client):
    """What the optimiser is handed: the demo CV, the Fernhill job with these keywords, and a CV
    showing three of its lines."""
    with transaction() as session:
        store = Store(session)
        cv = store.cv()
        job = store._job(store._job_row(JOB))
        evidence = store._evidence(JOB).model_copy(update={"keywords": KEYWORDS})
    return {"cv": cv, "job": job, "evidence": evidence, "doc": doc_for(cv)}


def run_optimiser(pack, note=None, cv=None):
    with transaction() as session:
        return optimise(
            session,
            pack["doc"],
            cv=cv or pack["cv"],
            evidence=pack["evidence"],
            job=pack["job"],
            cv_pages=1,
            note=note,
        )


@pytest.fixture
def model(monkeypatch):
    """The fake `cv.optimise`: `model.write(fn)` sets what it replies, from the targets it was sent."""

    class Fake:
        calls: list[dict] = []
        reply = staticmethod(lambda context: {"bullets": []})

        def write(self, fn):
            self.reply = staticmethod(fn)

    fake = Fake()
    fake.calls = []
    real = demo.respond

    def respond(task, variables, context, demo_input=None):
        if task != "cv.optimise":
            return real(task, variables, context, demo_input)
        fake.calls.append({"variables": variables, "targets": (context or {}).get("optimise", [])})
        return fake.reply(context or {})

    monkeypatch.setattr(demo, "respond", respond)
    return fake


def rewrite(bullet: str, text: str, sources: list[str], keyword: str) -> dict:
    return {"bullet": bullet, "text": text, "sources": sources, "keyword": keyword, "reason": f"Adds {keyword}."}


AZURE = "Built a FastAPI API gateway on Azure AI Foundry that scans prompts for personal data (regex and Presidio) before they reach LLMs."


def bullets(doc: TailoredCV) -> list[Cited]:
    return [b for s in doc.sections for i in s.items for b in i.bullets]


class TestTargets:
    def test_only_keywords_the_cv_backs_are_targeted(self, pack):
        from wera_agent.nodes.ats import build_ats

        report = build_ats(pack["doc"], cv=pack["cv"], evidence=pack["evidence"], cv_pages=1, job=pack["job"])
        found = targets(pack["doc"], pack["cv"], report.keywords, limit=5)
        terms = [t.keyword.term for t in found]
        assert "Kubernetes" not in terms  # nowhere in the CV
        assert "FastAPI" not in terms  # already in a line of work
        assert {"Azure", "Postmark"} <= set(terms)
        azure = next(t for t in found if t.keyword.term == "Azure")
        assert azure.role_id == "exp-idsu" and azure.lines == ["exp-idsu-02"]
        # Both of that role's lines, the one carrying fewer of the post's matched keywords first.
        assert azure.bullets == ["s0.i0.b1", "s0.i0.b0"]

    def test_the_prompt_carries_only_backed_targets_and_the_change_note(self, pack, model):
        run_optimiser(pack, note="Lead with the Azure work")
        sent = model.calls[0]["variables"]
        assert "Keyword: Azure" in sent["targets"]
        assert "Keyword: Kubernetes" not in sent["targets"]
        assert "Lead with the Azure work" in sent["note"]


class TestTheGuard:
    def test_a_backed_rewrite_is_applied_as_a_composed_line(self, pack, model):
        model.write(lambda c: {"bullets": [rewrite("s0.i0.b0", AZURE, ["exp-idsu-01", "exp-idsu-02"], "Azure")]})
        doc, report = run_optimiser(pack)
        line = doc.sections[0].items[0].bullets[0]
        assert line.text == AZURE and line.composed and line.source_ids == ["exp-idsu-01", "exp-idsu-02"]
        assert report.changes[0].before.startswith("Built a FastAPI API gateway that scans")
        assert report.changes[0].keyword == "Azure"
        assert report.score_after > report.score_before

    def test_a_rewrite_adding_a_skill_its_lines_dont_carry_is_refused(self, pack, model):
        # Stripe is one of the CV's skills, so the citation guard alone would let it through.
        text = AZURE.replace("LLMs.", "LLMs, billed through Stripe.")
        model.write(lambda c: {"bullets": [rewrite("s0.i0.b0", text, ["exp-idsu-01", "exp-idsu-02"], "Azure")]})
        doc, report = run_optimiser(pack)
        assert doc.sections[0].items[0].bullets[0].text == pack["doc"].sections[0].items[0].bullets[0].text
        assert [(r.rule, r.keyword) for r in report.refused] == [("unbacked", "Azure")]
        assert "Stripe" in report.refused[0].detail
        assert not report.changes

    def test_a_failed_citation_keeps_the_original_line(self, pack, model):
        model.write(
            lambda c: {
                "bullets": [
                    # A number the cited lines don't have.
                    rewrite(
                        "s0.i0.b0",
                        AZURE.replace("scans prompts", "scans 40% of prompts"),
                        ["exp-idsu-01", "exp-idsu-02"],
                        "Azure",
                    ),
                    # A line it wasn't given to cite.
                    rewrite(
                        "s0.i1.b0",
                        "Built a scouting platform on Next.js, sending email through Postmark.",
                        ["exp-fm-01", "exp-fm-03"],
                        "Postmark",
                    ),
                ]
            }
        )
        doc, report = run_optimiser(pack)
        assert [b.text for b in bullets(doc)] == [b.text for b in bullets(pack["doc"])]
        assert len(bullets(doc)) == 3  # nothing deleted
        assert {r.rule for r in report.refused} == {"numbers", "sources"}

    def test_a_rewrite_must_carry_its_keyword_and_keep_its_length(self, pack, model):
        long = AZURE + " " + "It ran every day across the organisation's internal workflow tooling." * 2
        model.write(
            lambda c: {
                "bullets": [
                    rewrite("s0.i0.b0", long, ["exp-idsu-01", "exp-idsu-02"], "Azure"),
                    rewrite(
                        "s0.i0.b1", "Wrote prompt templates for the redaction rules.", ["exp-idsu-03", "exp-idsu-02"], "Azure"
                    ),
                ]
            }
        )
        doc, report = run_optimiser(pack)
        assert {r.rule for r in report.refused} == {"length", "keyword"}
        assert not report.changes

    def test_the_page_limit_holds(self, pack, model, monkeypatch):
        real = ats_optimise.estimate_pages
        monkeypatch.setattr(
            ats_optimise, "estimate_pages", lambda d, cv: 1.4 if any(b.composed for b in bullets(d)) else real(d, cv)
        )
        model.write(lambda c: {"bullets": [rewrite("s0.i0.b0", AZURE, ["exp-idsu-01", "exp-idsu-02"], "Azure")]})
        doc, report = run_optimiser(pack)
        assert not any(b.composed for b in bullets(doc))
        assert [r.rule for r in report.refused] == ["pages"]

    def test_the_skills_line_stays_at_fifteen(self, pack, model):
        names = [f"Tool{n}" for n in range(25)]
        cv = pack["cv"].model_copy(update={"skills": [*pack["cv"].skills, *(Skill(name=n) for n in names)]})
        pack["evidence"] = pack["evidence"].model_copy(
            update={"keywords": [*KEYWORDS, *(JobKeyword(term=n, weight="required") for n in names)]}
        )
        doc, _ = run_optimiser(pack, cv=cv)
        assert len(doc.skills) <= 15


class TestTheLoop:
    def test_a_round_that_doesnt_raise_the_score_is_undone_and_stops(self, pack, model, monkeypatch):
        real = ats_optimise.build_ats

        def flat(*args, **kwargs):
            out = real(*args, **kwargs)
            out.score = 50.0
            return out

        monkeypatch.setattr(ats_optimise, "build_ats", flat)
        model.write(lambda c: {"bullets": [rewrite("s0.i0.b0", AZURE, ["exp-idsu-01", "exp-idsu-02"], "Azure")]})
        doc, report = run_optimiser(pack)
        assert report.stopped == "no_gain" and report.calls == 1
        assert report.rounds[0].rewritten == 1 and not report.rounds[0].kept
        assert not report.changes and not any(b.composed for b in bullets(doc))

    def test_a_small_gain_is_kept_and_stops_the_loop(self, pack, model, monkeypatch):
        monkeypatch.setattr(wera().optimise, "min_gain", 99.0)
        model.write(lambda c: {"bullets": [rewrite("s0.i0.b0", AZURE, ["exp-idsu-01", "exp-idsu-02"], "Azure")]})
        doc, report = run_optimiser(pack)
        assert report.stopped == "no_gain" and report.calls == 1 and len(report.changes) == 1

    def test_at_most_two_calls_however_many_rounds(self, pack, model, monkeypatch):
        """Each round rewrites the first bullet offered for the first target, and the score is
        made to rise by 5 each time it is measured, so only the caps can stop it."""
        monkeypatch.setattr(wera().optimise, "max_rounds", 5)
        real = ats_optimise.build_ats
        counter = {"n": 0}

        def rising(*args, **kwargs):
            out = real(*args, **kwargs)
            counter["n"] += 1
            out.score = 40.0 + 5 * counter["n"]
            return out

        monkeypatch.setattr(ats_optimise, "build_ats", rising)

        def first(context):
            t = context["optimise"][0]
            b = t["bullets"][0]
            text = f"{b['text'].rstrip('.')}, using {t['keyword']}."
            return {"bullets": [rewrite(b["bullet"], text, [*b["sources"], t["lines"][0]], t["keyword"])]}

        model.write(first)
        _doc, report = run_optimiser(pack)
        assert report.calls == 2 and len(model.calls) == 2 and len(report.rounds) == 2
        assert report.stopped in ("max_calls", "no_targets")
        assert len(report.changes) == 2

    def test_the_default_caps_are_two_rounds_and_two_calls(self):
        cfg = wera().optimise
        assert (cfg.max_rounds, cfg.max_calls, cfg.min_gain, cfg.max_targets) == (2, 2, 1, 5)
        assert wera().task("cv.optimise").tier == "optimise" and wera().task("cv.optimise").reasoning == "off"

    def test_a_failed_call_leaves_the_cv_as_tailored(self, pack, model):
        def down(context):
            raise ApiError(502, "LLM_UNAVAILABLE", "down")

        model.write(down)
        doc, report = run_optimiser(pack)
        assert report.stopped == "error" and not report.changes
        assert [b.text for b in bullets(doc)] == [b.text for b in bullets(pack["doc"])]


# ---------------------------------------------------------------------------
# In the pack: the step, the save, Undo, and the graph


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def drafted(client):
    """A first draft, ready for review, with the CV replaced by the three-line one above and the
    job's keywords by `KEYWORDS`."""
    with transaction() as session:
        store = Store(session)
        store.decide(JOB, DecisionIn(action="approve"))
        app_id = store.started_application_id
    run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
    with transaction() as session:
        store = Store(session)
        store.save_document(app_id, "cv", doc_for(store.cv()))
    return app_id


@pytest.fixture
def keywords(monkeypatch):
    real = Store._evidence
    monkeypatch.setattr(Store, "_evidence", lambda self, job_id: real(self, job_id).model_copy(update={"keywords": KEYWORDS}))


class TestInThePack:
    def test_the_optimise_document_is_saved_with_the_new_cv(self, drafted, keywords, model):
        model.write(lambda c: {"bullets": [rewrite("s0.i0.b0", AZURE, ["exp-idsu-01", "exp-idsu-02"], "Azure")]})
        with transaction() as session:
            drafting.step_optimise(session, drafted, note="Lead with Azure")
        assert "Lead with Azure" in model.calls[0]["variables"]["note"]
        with transaction() as session:
            store = Store(session)
            pack = store._load_pack(drafted)
            assert isinstance(pack.optimise, OptimiseReport)
            assert pack.optimise.changes[0].after == AZURE
            assert pack.cv.sections[0].items[0].bullets[0].composed
            assert pack.ats.score == pack.optimise.score_after

    def test_the_change_note_on_the_application_reaches_the_prompt(self, drafted, keywords, model):
        with transaction() as session:
            Store(session)._app_row(drafted).change_note = "Mention Azure"
        with transaction() as session:
            drafting.step_optimise(session, drafted, note=None)
        assert "Mention Azure" in model.calls[0]["variables"]["note"]

    def test_undo_restores_the_original_line(self, drafted, keywords, model):
        model.write(lambda c: {"bullets": [rewrite("s0.i0.b0", AZURE, ["exp-idsu-01", "exp-idsu-02"], "Azure")]})
        with transaction() as session:
            drafting.step_optimise(session, drafted, note=None)
        with transaction() as session:
            change = Store(session)._load_pack(drafted).optimise.changes[0]
        with transaction() as session:
            # G2-02's per-line edit, which is what the review screen's "Undo this change" sends.
            Store(session).edit_document(f"{drafted}.cv", DocumentPatchIn(target=change.location, text=change.before))
        with transaction() as session:
            store = Store(session)
            line = store._load_pack(drafted).cv.sections[0].items[0].bullets[0]
            master = {b.id: b.text for r in store.cv().experience for b in r.bullets}
            assert line.text == master["exp-idsu-01"] and line.edited_by_user


class TestVerbatim:
    def test_a_composed_bullet_passes_and_a_drifted_copy_still_fails(self, drafted):
        with transaction() as session:
            store = Store(session)
            doc = doc_for(store.cv())
            doc.sections[0].items[0].bullets[0] = Cited(text=AZURE, source_ids=["exp-idsu-01", "exp-idsu-02"], composed=True)
            store.save_document(drafted, "cv", doc)  # does not raise
            doc.sections[0].items[0].bullets[1].text += " Quickly."
            with pytest.raises(ApiError, match="doesn't match the master CV"):
                store.save_document(drafted, "cv", doc)


class TestTheGraph:
    @pytest.fixture
    def calls(self, monkeypatch):
        seen: list[str | None] = []
        monkeypatch.setattr(drafting, "step_optimise", lambda session, app_id, *, note: seen.append(note))
        return seen

    def test_a_first_draft_skips_the_optimiser(self, client, calls):
        with transaction() as session:
            store = Store(session)
            store.decide(JOB, DecisionIn(action="approve"))
            app_id = store.started_application_id
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        assert calls == []
        with transaction() as session:
            assert not Store(session).has_document(app_id, "optimise")

    def test_a_redraft_runs_it_with_the_note(self, client, calls):
        with transaction() as session:
            store = Store(session)
            store.decide(JOB, DecisionIn(action="approve"))
            app_id = store.started_application_id
        run(graph.advance(JOB, {"action": "approve", "applicationId": app_id}))
        run(graph.advance(JOB, {"action": "request_changes", "note": "Lead with Azure"}))
        assert calls == ["Lead with Azure"]


class TestTheEval:
    """`python -m wera_agent.evaluate optimise`, with the fake model: each model runs every pack, the
    tier is overridden for the run and put back after, and the report reads off `llm_calls`."""

    def test_each_model_runs_every_pack_and_the_report_says_so(self, pack, model):
        from wera_agent import evaluate

        model.write(lambda c: {"bullets": [rewrite("s0.i0.b0", AZURE, ["exp-idsu-01", "exp-idsu-02"], "Azure")]})
        packs = [
            evaluate.OptimisePack(
                app_id="app-1", job=pack["job"], cv=pack["cv"], evidence=pack["evidence"], doc=pack["doc"], note="Lead with Azure"
            )
        ]
        before = wera().models.optimise, list(wera().fallbacks.get("optimise", []))
        report = evaluate.optimise_packs(packs, ["model/a", "model/b"], ran_at="2026-11-22T10:00:00Z")

        assert [(r.model, r.rewritten) for r in report.rows] == [("model/a", 1), ("model/b", 1)]
        assert all(1 <= r.calls <= 2 for r in report.rows)
        assert all(r.after > r.before and r.error is None for r in report.rows)
        # Demo mode records the tier's model as `demo:<model>`: the override reached the gateway.
        assert report.rows[0].models_used == ["demo:model/a"] and report.rows[1].models_used == ["demo:model/b"]
        assert (wera().models.optimise, list(wera().fallbacks.get("optimise", []))) == before
        text = evaluate.optimise_as_markdown(report)
        assert "`model/a`" in text and "Bullets rewritten" in text and AZURE in text

    def test_it_refuses_to_run_outside_an_eval_database(self, client):
        from wera_agent import evaluate

        with pytest.raises(SystemExit, match="_eval"):
            evaluate.run_optimise(source_url="postgresql://localhost:5432/wera", n=1, models=["a"])
