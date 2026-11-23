"""UPSK-08 (22 Nov): the Upskills analyst labels and explains the gaps; the counts stay in code.

One fast-tier call a search, after it, for the gap terms not labelled for this CV version. What
matters: only new terms are sent and none twice; a bad or chained `same_as` changes nothing; a
merge adds the jobs, each once with its own lift; a trait is never a gap; the page's fingerprint
moves when notes land; and a failure costs the notes, never the search. The model is a fake.
"""

import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from wera_agent.clock import iso
from wera_agent.config import wera
from wera_agent.db.models import SkillNoteRow
from wera_agent.db.session import transaction
from wera_agent.errors import ApiError
from wera_agent.llm.prompts import CLOSE, OPEN
from wera_agent.schemas.llm_outputs import EVIDENCE_LIMIT, UpskillsNotes
from wera_agent.services import upskills, upskills_analyst
from wera_agent.services.store import Store

from .test_upskills import NOW, find, job, scored


@pytest.fixture(autouse=True)
def analyst_on(monkeypatch):
    """The suite starts with the analyst off (conftest); these tests are about it, at its default."""
    monkeypatch.setattr(wera().upskills, "analyst_terms", 40)


class Fake:
    """`structured` for `upskills.analyse`: records what it was sent, answers with `answer`
    (a function of the terms sent) validated like a real reply."""

    def __init__(self, answer=None):
        self.calls: list[dict] = []
        self.answer = answer or (lambda terms: [{"term": t, "kind": "tool", "evidence": f"A project using {t}"} for t in terms])

    def __call__(self, session, task, Output, variables, *, context=None, **_):
        assert task == "upskills.analyse" and Output is UpskillsNotes
        terms = [t["term"] for t in context["upskills"]]
        self.calls.append({"terms": terms, "prompt": variables["terms"]})
        return UpskillsNotes.model_validate({"notes": self.answer(terms)}, context=context)


@pytest.fixture
def fake(monkeypatch, client):
    """The fake model, with the seeded demo jobs' gaps labelled already, so what a test sends is
    what it added."""
    with transaction() as session:
        cv = Store(session).cv()
        batch, _others, _n = upskills_analyst.pending(session, cv, now=NOW, cap=1000)
    for t in batch:
        note(t.key)
    model = Fake()
    monkeypatch.setattr(upskills_analyst, "structured", model)
    return model


def jobs(*rows):
    """Scored jobs: each row is (i, match, *terms), every term required."""
    with transaction() as session:
        store = Store(session)
        for i, match, *terms in rows:
            scored(store, job(i), *((t, "required") for t in terms), match=match)


def note(term: str, *, kind: str = "tool", same_as: str | None = None, evidence: str | None = None, at: int = NOW) -> None:
    with transaction() as session:
        session.merge(
            SkillNoteRow(
                term=term,
                cv_version=Store(session).cv().version,
                kind=kind,
                same_as=same_as,
                evidence=evidence,
                family_note=None,
                model="fake",
                created_at=iso(at),
            )
        )


def report():
    with transaction() as session:
        store = Store(session)
        return upskills.report(session, store.cv(), now=NOW)


def analyse(**kw):
    with transaction() as session:
        cv = Store(session).cv()
    return upskills_analyst.analyse(cv, now=NOW, **kw)


def stored() -> dict[str, SkillNoteRow]:
    with transaction() as session:
        rows = session.scalars(select(SkillNoteRow)).all()
        session.expunge_all()
        return {r.term: r for r in rows}


@pytest.mark.usefixtures("client")
class TestWhatIsSent:
    def test_only_terms_not_yet_labelled_are_sent(self, fake):
        jobs((1, 5, "Kafka", "Snowflake"))
        note("kafka")
        got = analyse()
        assert fake.calls[0]["terms"] == ["Snowflake"] and (got.sent, got.labelled) == (1, 1)

    def test_a_term_labelled_once_is_never_sent_again_and_nothing_new_makes_no_call(self, fake):
        jobs((1, 5, "Kafka", "Snowflake"))
        assert analyse().labelled == 2
        again = analyse()
        assert len(fake.calls) == 1 and (again.pending, again.sent) == (0, 0)

    def test_at_most_the_configured_terms_a_call(self, fake, monkeypatch):
        monkeypatch.setattr(wera().upskills, "analyst_terms", 2)
        jobs((1, 5, "Kafka", "Snowflake", "Airflow", "Looker"))
        got = analyse()
        assert len(fake.calls[0]["terms"]) == 2 and got.pending == 4

    def test_off_sends_nothing(self, fake, monkeypatch):
        monkeypatch.setattr(wera().upskills, "analyst_terms", 0)
        jobs((1, 5, "Kafka"))
        assert analyse().sent == 0 and fake.calls == []

    def test_a_dry_run_counts_and_calls_nothing(self, fake):
        jobs((1, 5, "Kafka", "Snowflake"))
        got = analyse(dry_run=True)
        assert (got.sent, sorted(got.terms), fake.calls) == (2, ["Kafka", "Snowflake"], [])
        assert "kafka" not in stored()

    def test_the_posts_words_go_in_as_untrusted_data_with_the_status_from_code(self, fake):
        jobs((1, 5, "Kafka"))
        analyse()
        prompt = fake.calls[0]["prompt"]
        assert prompt.startswith(OPEN) and prompt.rstrip().endswith(CLOSE)
        assert "status: not on the CV" in prompt

    def test_a_note_for_a_term_not_sent_is_dropped_and_lengths_are_capped(self, fake, monkeypatch):
        fake = Fake(
            lambda terms: [{"term": "Invented", "kind": "tool"}, {"term": terms[0], "kind": "tool", "evidence": "word " * 60}]
        )
        monkeypatch.setattr(upskills_analyst, "structured", fake)
        jobs((1, 5, "Kafka"))
        analyse()
        rows = stored()
        assert "invented" not in rows and len(rows["kafka"].evidence) <= EVIDENCE_LIMIT
        assert rows["kafka"].model != "fake"  # written by this pass


@pytest.mark.usefixtures("client")
class TestApplied:
    def test_a_merge_sums_jobs_and_weights_each_with_its_own_lift(self):
        jobs((1, 3, "Data Build Tool"), (2, 10, "dbt"))  # a poor match (lift 2), a strong one (lift 1)
        before = report()
        poor, strong = find(before, "Data Build Tool"), find(before, "dbt")
        assert (poor.jobs, strong.jobs) == (1, 1) and poor.score > strong.score
        note("data build", same_as="dbt")
        merged = find(report(), "dbt")
        assert find(report(), "Data Build Tool").term == "dbt"  # a pack's gap under either spelling finds it
        assert (merged.term, merged.jobs, merged.poor) == ("dbt", 2, 1)
        assert merged.score == pytest.approx(poor.score + strong.score)
        assert merged.merged == ["Data Build Tool"] and "Data Build Tool" in merged.spellings
        assert sum(d.jobs for d in report().demand if d.term in ("dbt", "Data Build Tool")) == 2

    def test_a_job_asking_both_spellings_counts_once(self):
        jobs((1, 5, "Data Build Tool", "dbt"))
        note("data build", same_as="dbt")
        assert find(report(), "dbt").jobs == 1

    def test_a_same_as_naming_no_term_of_the_report_is_ignored(self):
        jobs((1, 5, "Kafka"))
        note("kafka", same_as="pulsar")
        assert find(report(), "Kafka").jobs == 1

    def test_a_chain_is_never_followed(self):
        jobs((1, 5, "Kafka"), (2, 5, "Snowflake"), (3, 5, "Airflow"))
        note("kafka", same_as="snowflake")
        note("snowflake", same_as="airflow")
        out = report()
        assert find(out, "Kafka").jobs == 1  # Kafka → Snowflake dropped: Snowflake is merged itself
        assert find(out, "Airflow").jobs == 2 and find(out, "Airflow").merged == ["Snowflake"]

    def test_two_terms_named_as_each_other_become_one_row_under_the_shorter(self):
        """22 Nov, live: "derivatives" ↔ "financial derivatives" were both refused as a chain."""
        jobs((1, 5, "Derivatives"), (2, 5, "Financial Derivatives"))
        note("derivatives", same_as="financial derivatives")
        note("financial derivatives", same_as="derivatives")
        out = report()
        assert find(out, "Derivatives").jobs == 2 and find(out, "Derivatives").merged == ["Financial Derivatives"]

    def test_the_model_merging_into_a_term_it_was_not_sent_is_checked_against_the_report(self, fake, monkeypatch):
        fake = Fake(lambda terms: [{"term": t, "kind": "tool", "sameAs": "Made Up" if t == "Kafka" else None} for t in terms])
        monkeypatch.setattr(upskills_analyst, "structured", fake)
        jobs((1, 5, "Kafka", "Snowflake"))
        analyse()
        assert stored()["kafka"].same_as is None and find(report(), "Kafka").jobs == 1

    def test_a_trait_is_never_a_gap(self):
        jobs((1, 5, "Kafka", "Grit"))
        note("grit", kind="trait")
        out = report()
        assert find(out, "Grit") is None and "Grit" not in {d.term for d in out.demand}

    def test_kind_refines_the_category_and_the_evidence_reaches_the_row(self):
        jobs((1, 5, "Kafka"))
        note("kafka", kind="qualification", evidence="A streaming project")
        row = find(report(), "Kafka")
        assert (row.category, row.evidence) == ("qualification", "A streaming project")

    def test_without_notes_the_report_is_unchanged(self, fake):
        jobs((1, 5, "Kafka"), (2, 3, "Snowflake"))
        before = report().model_dump()
        note("unrelated")
        assert report().model_dump() == before


@pytest.mark.usefixtures("client")
def test_the_version_moves_when_notes_land(fake):
    jobs((1, 5, "Kafka"))
    with transaction() as session:
        store = Store(session)
        first = upskills.version(session, store.cv())
    analyse()
    with transaction() as session:
        store = Store(session)
        assert upskills.version(session, store.cv()) != first


@pytest.mark.usefixtures("client")
class TestAFailure:
    def test_a_refused_reply_costs_the_notes(self, fake, monkeypatch):
        def refused(*_a, **_k):
            raise ApiError(502, "LLM_BAD_OUTPUT", "no")

        monkeypatch.setattr(upskills_analyst, "structured", refused)
        jobs((1, 5, "Kafka"))
        with transaction() as session:
            cv = Store(session).cv()
        got = upskills_analyst.after_search(cv, now=NOW)
        assert got.error == "LLM_BAD_OUTPUT" and "kafka" not in stored()

    def test_anything_else_is_logged_and_never_raised(self, fake, monkeypatch):
        def broken(*_a, **_k):
            raise RuntimeError("boom")

        monkeypatch.setattr(upskills_analyst, "structured", broken)
        jobs((1, 5, "Kafka"))
        with transaction() as session:
            cv = Store(session).cv()
        assert upskills_analyst.after_search(cv, now=NOW) is None
        assert "kafka" not in stored()


class TestAfterASearch:
    @pytest.fixture
    def background(self, client, monkeypatch):
        from wera_agent.graphs import search_pipeline
        from wera_agent.services import near_misses
        from wera_agent.settings import get_settings

        monkeypatch.setattr(search_pipeline, "db_engine", lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
        monkeypatch.setattr(get_settings(), "wera_demo_mode", False)
        order, done = [], threading.Event()
        monkeypatch.setattr(near_misses, "capture", lambda *_a, **_k: order.append("capture") or near_misses.Captured())

        def labelled(cv, *, now):
            order.append(("analyst", threading.current_thread().name))
            done.set()

        monkeypatch.setattr(upskills_analyst, "after_search", labelled)
        return search_pipeline, order, done

    def test_once_a_search_after_the_reads_on_the_near_miss_thread(self, background):
        search_pipeline, order, done = background
        search_pipeline._near_misses(search_pipeline.Piped(), {}, [], cv=object(), rank=None, now=0, at=0.9)
        assert done.wait(5)
        assert order == ["capture", ("analyst", "near-misses")]

    def test_runs_with_near_misses_off(self, background, monkeypatch):
        search_pipeline, order, done = background
        monkeypatch.setattr(wera().upskills, "near_miss_per_search", 0)
        search_pipeline._near_misses(search_pipeline.Piped(), {}, [], cv=object(), rank=None, now=0, at=0.9)
        assert done.wait(5) and order == [("analyst", "near-misses")]

    def test_the_demo_makes_no_call(self, client, monkeypatch):
        from wera_agent.graphs import search_pipeline

        calls = []
        monkeypatch.setattr(upskills_analyst, "after_search", lambda *a, **k: calls.append(a))
        search_pipeline._near_misses(search_pipeline.Piped(), {}, [], cv=object(), rank=None, now=0, at=0.9)
        assert calls == []


@pytest.mark.usefixtures("client")
def test_through_the_gateway_in_the_demo_every_term_sent_is_labelled_and_the_call_recorded():
    from wera_agent.db.models import LlmCallRow

    jobs((1, 5, "Kafka"))
    got = analyse()
    assert got.sent and got.labelled == got.sent and got.error is None
    rows = stored()
    assert rows["kafka"].evidence and rows["kafka"].model.startswith("demo:")
    with transaction() as session:
        assert session.scalar(select(func.count()).select_from(LlmCallRow).where(LlmCallRow.task == "upskills.analyse")) == 1
