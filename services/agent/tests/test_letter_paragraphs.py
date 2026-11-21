"""LTR-01, 20 Nov: the cover letter written paragraph by paragraph (`letters.mode: paragraphs`).

What 20 Nov's letters lost, and what holds each part here: a paragraph the guard fails is written
again, not cut; the employer's words survive the guard; the opening cites the degree it
introduces the applicant by; and the letter as assembled is never short because of the guard.
Plus the two fixes to `single` mode, kept for rollback, and the casing and naming fixes (M6).
"""

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from wera_agent.config import wera
from wera_agent.domain.guard import institution_violations
from wera_agent.errors import ApiError
from wera_agent.llm import demo
from wera_agent.nodes import tailor
from wera_agent.nodes.letter_plan import SECTIONS, section_plans
from wera_agent.schemas.llm_outputs import SummaryDraft

JOB = "job-fernhill-mle"
SPOIL = " I ran the Kubernetes platform for 40 teams."


@pytest.fixture
def inp(store):
    """The demo job's pack input with its tailored CV, as `step_letter` has it."""
    pack = store.pack_input(JOB)
    doc = tailor.tailor(store.s, pack)
    return replace(pack, doc=doc)


def spy(monkeypatch, change=None):
    """Records each `letter.paragraph` call's section, and lets a test change its reply."""
    real = demo.respond
    calls: list[str] = []

    def respond(task, variables, context, demo_input=None):
        out = real(task, variables, context, demo_input)
        if task == "letter.paragraph":
            section = context["plan"].section
            calls.append(section)
            if change:
                out = change(section, calls.count(section), out, variables) or out
        return out

    monkeypatch.setattr(demo, "respond", respond)
    return calls


class TestThePlan:
    def test_the_opening_cites_education(self, inp):
        """20 Nov: the opening was told to cite the degree, which had no id, so it cited nothing
        and was dropped for it. Education entries are lines now, and the plan hands it one."""
        plans = section_plans(inp.cv, inp.doc, inp.evidence, post=inp.job.description_md or "", company=inp.job.company)
        assert [p.section for p in plans] == list(SECTIONS)
        assert inp.cv.education[0].id in plans[0].line_ids
        assert plans[3].line_ids == ()  # the close cites nothing

    def test_every_quote_offered_is_in_the_post(self, inp):
        post = " ".join((inp.job.description_md or "").split())
        plans = section_plans(inp.cv, inp.doc, inp.evidence, post=inp.job.description_md or "", company=inp.job.company)
        quotes = [q.text for p in plans for q in p.quotes]
        assert quotes and all(q in post for q in quotes)
        assert len(quotes) == len(set(quotes))  # one quote, one paragraph

    def test_the_written_opening_cites_its_degree(self, store, inp):
        letter = tailor.letter(store.s, inp, inp.doc)
        assert letter.paragraphs[0].section == "opening_hook"
        assert inp.cv.education[0].id in letter.paragraphs[0].source_ids


class TestAFailingParagraph:
    def test_is_written_again_not_deleted(self, store, inp, monkeypatch):
        calls = spy(
            monkeypatch,
            lambda section, n, out, _v: (
                {**out, "text": out["text"] + SPOIL} if section == "core_value_proposition" and n == 1 else None
            ),
        )
        letter = tailor.letter(store.s, inp, inp.doc)
        assert calls.count("core_value_proposition") == 2  # written again, once
        assert len(calls) == 5  # and only that one
        assert [p.section for p in letter.paragraphs] == list(SECTIONS)
        assert not any("Kubernetes" in p.text for p in letter.paragraphs)
        assert letter.guard.pass_ and letter.guard.repaired

    def test_is_told_what_failed_in_its_own_words(self, store, inp, monkeypatch):
        notes: list[str] = []

        def change(section, n, out, variables):
            if section == "core_value_proposition":
                notes.append(variables["note"])
                if n == 1:
                    return {**out, "text": out["text"] + SPOIL}
            return None

        spy(monkeypatch, change)
        tailor.letter(store.s, inp, inp.doc)
        assert "40" in notes[1] and "Kubernetes platform" in notes[1]

    def test_that_fails_twice_is_built_from_its_own_lines(self, store, inp, monkeypatch):
        spy(
            monkeypatch,
            lambda section, _n, out, _v: {**out, "text": out["text"] + SPOIL} if section == "core_value_proposition" else None,
        )
        letter, stats = tailor.write_letter(store.s, inp, inp.doc)
        assert stats.regenerated == 1 and stats.templated == 1
        proof = letter.paragraphs[1]
        assert proof.section == "core_value_proposition" and proof.source_ids and "Kubernetes" not in proof.text
        assert [v.rule for v in letter.guard.violations] == ["replaced"]
        assert "paragraph 2" in letter.guard.violations[0].detail

    def test_a_letter_is_never_under_150_because_of_the_guard(self, store, inp, monkeypatch):
        """Every paragraph fails every time: what is left is four paragraphs from the CV's own
        lines, not a letter cut down to one."""
        spy(monkeypatch, lambda _s, _n, out, _v: {**out, "text": out["text"] + SPOIL})
        letter, stats = tailor.write_letter(store.s, inp, inp.doc)
        assert len(letter.paragraphs) == 4 and stats.templated == 4
        assert letter.word_count >= tailor.TARGET_MIN_WORDS

    def test_a_short_letter_has_its_thinnest_paragraph_written_again(self, store, inp, monkeypatch):
        def change(section, n, out, variables):
            if "under the 150" in variables["note"]:
                return None  # the length rewrite comes back full
            return {**out, "text": " ".join(out["text"].split()[:20]).rstrip(".,;:") + "."}

        calls = spy(monkeypatch, change)
        letter, stats = tailor.write_letter(store.s, inp, inp.doc)
        assert stats.lengthened == 1 and len(calls) == 5
        assert letter.word_count > 4 * 21

    def test_a_long_letter_has_its_longest_paragraph_written_again(self, store, inp, monkeypatch):
        """20 Nov: 2 of 20 eval letters came to 233 and 250 words. Once, the paragraph furthest
        over its budget is written again, and kept if it comes back shorter and clean. The ceiling
        is lowered here so the demo's ordinary letter is the long one."""
        monkeypatch.setattr(tailor, "TARGET_MAX_WORDS", 60)
        notes: list[str] = []

        def change(section, n, out, variables):
            if "over the 60" in (variables.get("note") or ""):
                notes.append(section)
                return {**out, "text": " ".join(out["text"].split()[:12]).rstrip(".,;:") + "."}
            return None

        calls = spy(monkeypatch, change)
        letter, stats = tailor.write_letter(store.s, inp, inp.doc)
        # Still over 60 after the first, so a second paragraph is written again (21 Nov), and no more.
        assert stats.shortened == 2 and len(calls) == 6 and len(notes) == 2 and notes[0] != notes[1]
        shortened = next(p for p in letter.paragraphs if p.section == notes[0])
        assert len(shortened.text.split()) <= 13

    def test_every_call_failing_fails_the_letter(self, store, inp, monkeypatch):
        """The key, the credit or the budget, not the letter: no letter of four templates."""
        real = demo.respond

        def down(task, variables, context, demo_input=None):
            if task == "letter.paragraph":
                raise ApiError(502, "LLM_UPSTREAM", "down")
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", down)
        with pytest.raises(ApiError):
            tailor.letter(store.s, inp, inp.doc)


class TestTheEmployersWords:
    def test_survive_the_guard(self, store, inp, monkeypatch):
        """The company's name, the role and the post's own words, with a number in them, pass
        as the employer speaking — and the paragraph that passes is the one written."""
        quote = next(
            q
            for p in section_plans(inp.cv, inp.doc, inp.evidence, post=inp.job.description_md or "", company=inp.job.company)
            for q in p.quotes
        )
        text = f"At {inp.job.company} the {inp.job.title} post says “{quote.text}”, and that is why I am writing to the team."

        spy(
            monkeypatch,
            lambda section, _n, out, _v: {**out, "text": text} if section == "strategic_alignment_and_culture" else None,
        )
        letter = tailor.letter(store.s, inp, inp.doc)
        assert letter.paragraphs[2].text == text
        assert inp.job.company in letter.paragraphs[2].text

    def test_come_back_after_a_repair_in_single_mode(self, store, inp, monkeypatch):
        """The `_without` loss: a repaired paragraph used to be printed without the company's
        name, because the repair was handed the paragraph with it cut out."""
        monkeypatch.setattr(wera().letters, "mode", "single")
        real = demo.respond
        seen: list[str] = []

        def respond(task, variables, context, demo_input=None):
            out = real(task, variables, context, demo_input)
            if task == "letter.write":
                out["paragraphs"][1]["text"] = f"I want to bring this to {inp.job.company}." + SPOIL
            if task == "guard.repair":
                seen.append(variables["text"])
                kept = variables["text"].replace(SPOIL, "")
                return {"item": {"text": kept, "sourceIds": ["exp-idsu-01"]}}
            return out

        monkeypatch.setattr(demo, "respond", respond)
        letter = tailor.letter(store.s, inp, inp.doc)
        assert seen and inp.job.company not in seen[0] and "⟦employer-" in seen[0]
        assert any(p.text.startswith(f"I want to bring this to {inp.job.company}.") for p in letter.paragraphs)

    def test_mask_round_trips(self):
        text, mapping = tailor.mask(
            "Fernhill says “we ship 3 times a day at Fernhill”.", ["we ship 3 times a day at Fernhill", "Fernhill"]
        )
        assert "3" not in text and "Fernhill" not in text
        assert tailor.unmask(text, mapping) == "Fernhill says “we ship 3 times a day at Fernhill”."


class TestSingleMode:
    @pytest.fixture(autouse=True)
    def single(self, monkeypatch):
        monkeypatch.setattr(wera().letters, "mode", "single")

    def test_a_failing_second_attempt_keeps_the_first(self, store, inp, monkeypatch):
        """20 Nov: the second `letter.write` had no `try`, so a good first letter went with the
        error and the pack failed."""
        real = demo.respond
        calls: list[int] = []

        def respond(task, variables, context, demo_input=None):
            if task == "letter.write":
                calls.append(1)
                if len(calls) == 2:
                    raise ApiError(502, "LLM_UPSTREAM", "down")
                out = real(task, variables, context, demo_input)
                for para in out["paragraphs"]:  # short, so it is asked for again
                    para["text"] = " ".join(para["text"].split()[:33]).rstrip(".,;") + "."
                return out
            return real(task, variables, context, demo_input)

        monkeypatch.setattr(demo, "respond", respond)
        letter, stats = tailor.write_letter(store.s, inp, inp.doc)
        assert len(calls) == 2 and stats.failed_calls == 1
        assert letter.paragraphs and letter.word_count < tailor.TARGET_MIN_WORDS  # the first, kept

    def test_short_reads_what_the_guard_left(self, store, inp, monkeypatch):
        """A repair that shrank a paragraph left a letter the model's own count called long
        enough; the guard's count is the one asked about now."""
        real = demo.respond
        notes: list[str] = []

        def respond(task, variables, context, demo_input=None):
            out = real(task, variables, context, demo_input)
            if task == "letter.write":
                notes.append(variables["note"])
                if len(notes) == 1:
                    for para in out["paragraphs"][1:3]:
                        para["text"] += SPOIL
            if task == "guard.repair":
                return {"item": {"text": "I built a FastAPI API gateway.", "sourceIds": ["exp-idsu-01"]}}
            return out

        monkeypatch.setattr(demo, "respond", respond)
        written = sum(len(p["text"].split()) for p in real("letter.write", {}, {"cv": inp.cv}, inp)["paragraphs"])
        assert written >= tailor.TARGET_MIN_WORDS  # what the model wrote was long enough…
        tailor.letter(store.s, inp, inp.doc)
        assert len(notes) == 2 and "150–230" in notes[1]  # …and what the guard left wasn't


class TestNames:
    def test_the_sign_off_is_in_ordinary_case(self, store, inp):
        cv = inp.cv.model_copy(update={"person": inp.cv.person.model_copy(update={"name": "ELEANOR VANCE"})})
        for mode in ("paragraphs", "single"):
            letter, _ = tailor.write_letter(store.s, replace(inp, cv=cv), inp.doc, mode=mode)
            assert letter.signoff.endswith("\nEleanor Vance"), mode

    @pytest.mark.parametrize(
        "text,flagged",
        [
            ("I studied at the LSE School of Economics.", True),
            ("I studied at the London School.", True),
            ("I studied at the London School of Economics and Political Science (LSE).", False),
            ("I studied at the London School of Economics and Political Science.", False),
            ("I studied at LSE, and ran the LSE Investment Society.", False),
            ("I studied at the London School of Economics.", False),  # the summary writes it so
            ("I live in London and want to work at Goldman Sachs.", False),
        ],
    )
    def test_an_institution_is_named_as_the_cv_names_it(self, demo_cv, text, flagged):
        """20 Nov: a live letter wrote "the LSE School of Economics" for "London School of
        Economics and Political Science (LSE)"."""
        education = [
            demo_cv.education[0].model_copy(update={"institution": "London School of Economics and Political Science (LSE)"})
        ]
        summary = demo_cv.summary.model_copy(update={"text": "Economics graduate from the London School of Economics."})
        cv = demo_cv.model_copy(update={"education": education, "summary": summary})
        assert bool(institution_violations("p0", text, cv)) is flagged

    def test_a_misnamed_institution_is_written_again(self, store, inp, monkeypatch):
        name = inp.cv.education[0].institution
        wrong = f"I studied at the Westmoor University School of Computing. The {inp.job.title} role is what I want next."
        assert "Westmoor" in name
        calls = spy(
            monkeypatch, lambda section, n, out, _v: {**out, "text": wrong} if section == "opening_hook" and n == 1 else None
        )
        letter = tailor.letter(store.s, inp, inp.doc)
        assert calls.count("opening_hook") == 2 and letter.paragraphs[0].text != wrong


class TestInParallel:
    def test_on_postgres_the_calls_run_at_once_and_come_back_in_order(self, client, monkeypatch):
        """Each in its own unit of work, since a session is not shared between threads. Out of demo
        mode: the demo answers from code, and drafts its seed packs before anything is committed."""
        from wera_agent.settings import get_settings

        monkeypatch.setattr(get_settings(), "wera_demo_mode", False)
        postgres = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
        barrier = threading.Barrier(4, timeout=5)

        def call(n: int):
            def run(session):
                barrier.wait()  # all four in flight together, or this times out
                assert session is not postgres
                return n

            return run

        assert tailor._in_parallel(postgres, [call(n) for n in range(4)]) == [0, 1, 2, 3]

    def test_an_error_is_returned_in_its_place(self, client):
        postgres = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))

        def fails(_s):
            raise ApiError(502, "LLM_UPSTREAM", "down")

        out = tailor._in_parallel(postgres, [lambda _s: 1, fails])
        assert out[0] == 1 and isinstance(out[1], ApiError)


class TestTheSummary:
    def test_holds_40_to_100_words(self, demo_cv):
        """The JobLeads review's 50–100 (19 Nov): `SUMMARY_WRITE` asks for 50–90."""
        ids = [demo_cv.experience[0].bullets[0].id]
        for n, ok in ((39, False), (40, True), (100, True), (101, False)):
            draft = {"text": " ".join(["word"] * n), "sourceIds": ids}
            if ok:
                SummaryDraft.model_validate(draft, context={"cv": demo_cv})
            else:
                with pytest.raises(ValueError):
                    SummaryDraft.model_validate(draft, context={"cv": demo_cv})


class TestQuotes:
    def test_a_quote_may_add_punctuation_and_an_ellipsis(self):
        post = "a specialist global valuations firm is expanding their london office and hiring an analyst."
        assert tailor.quoted_from("A specialist global valuation firm is expanding.", post) is False
        assert tailor.quoted_from("a specialist global valuations firm is expanding their London office.", post)
        assert tailor.quoted_from("A specialist global valuations firm … hiring an Analyst.", post)
        assert not tailor.quoted_from("a specialist firm hiring", post)

    def test_the_role_by_its_short_name_is_the_employers(self):
        assert tailor.title_forms("Investment Banking Analyst, Consumer & Retail") == [
            "Investment Banking Analyst, Consumer & Retail",
            "Investment Banking Analyst",
        ]
        assert tailor.title_forms("AI Engineer") == ["AI Engineer"]


class TestWorkRights:
    def test_a_quote_about_visas_is_never_offered(self, inp):
        """20 Nov: a company paragraph quoted the post's sponsorship line and claimed the right to
        work from the applicant's university. WERA holds no such fact, so no paragraph gets the words."""
        post = (inp.job.description_md or "") + "\n\nWe offer hybrid working and visa sponsorship for this role."
        plans = section_plans(inp.cv, inp.doc, inp.evidence, post=post, company=inp.job.company)
        assert not any("visa" in q.text.lower() for p in plans for q in p.quotes)


class TestTheDemoWritesInOrder:
    def test_demo_mode_never_opens_a_second_unit_of_work(self, client):
        """The demo's packs are drafted while the database is being seeded, in one uncommitted
        transaction; a call in its own transaction failed as "not set up yet" on Postgres (20 Nov)."""
        postgres = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
        seen = []
        assert tailor._in_parallel(postgres, [lambda s: seen.append(s) or 1, lambda s: seen.append(s) or 2]) == [1, 2]
        assert all(s is postgres for s in seen)


class TestNoPreferenceNobodyStated:
    """20 Nov, live: three letters said a hybrid policy "suits my preference for flexible
    arrangements" for a person whose profile is open to any way of working."""

    def test_a_stated_preference_is_caught_when_the_profile_holds_none(self):
        from types import SimpleNamespace

        open_to_any = SimpleNamespace(remote="any")
        text = 'The role offers "a hybrid working model". This structure suits my preference for flexible arrangements.'
        assert "preference for flexible" in tailor.stated_preference(text, open_to_any)

    def test_a_preference_the_profile_states_is_fine(self):
        from types import SimpleNamespace

        text = "Remote working suits my preference for working from home."
        assert tailor.stated_preference(text, SimpleNamespace(remote="remote_only")) is None

    def test_other_preferences_are_not_working_patterns(self):
        from types import SimpleNamespace

        text = "I prefer problems where the numbers have to reconcile."
        assert tailor.stated_preference(text, SimpleNamespace(remote="any")) is None

    def test_the_company_paragraph_takes_the_employers_words_before_its_policy(self, inp):
        post = (inp.job.description_md or "") + "\n\nWe offer a hybrid working model with three days in the office."
        plans = section_plans(inp.cv, inp.doc, inp.evidence, post=post, company=inp.job.company)
        works = plans[2].quotes
        assert not works or all(q.kind != "policy" for q in works) or len({q.kind for q in works}) == 1
