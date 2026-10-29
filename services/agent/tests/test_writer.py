"""The demo writer (ported from the web app's mock writer)."""

import pytest

from wera_agent.domain.vocab import cv_line_index, skill_text
from wera_agent.llm.demo import PackInput, generate_pack
from wera_agent.nodes.ats import tailored_text
from wera_agent.services.demo_data import seed_to_job

FERNHILL = 0  # Fernhill AI, Greenhouse


@pytest.fixture
def pack(demo_cv, demo_jobs):
    seed = demo_jobs["shortlist"][FERNHILL]
    job, evidence, questions = seed_to_job(seed, 0)
    return generate_pack(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)), seed


def test_cites_a_real_master_cv_line_for_every_bullet_and_passes_the_guard(pack, demo_cv):
    p, _ = pack
    lines = cv_line_index(demo_cv)
    bullets = [b for s in p.cv.sections for i in s.items for b in i.bullets]
    assert bullets
    assert all(b.source_ids and all(i in lines for i in b.source_ids) for b in bullets)
    assert p.cv.guard.pass_ and p.letter.guard.pass_


class TestTheProfessionalProfileSurvives:
    """TAIL-07. The summary is the line most likely to fail a strict citation check, because
    summarising *is* paraphrasing across the whole document — and it was being dropped, leaving
    a tailored CV with no profile at the top."""

    def test_the_tailored_cv_carries_a_profile(self, pack):
        p, _ = pack
        assert p.cv.summary.text.strip()
        assert p.cv.summary.source_ids

    def test_a_rejected_summary_falls_back_to_the_applicants_own(self, demo_cv):
        """Not to nothing. The master summary is their own words, already in the CV, and cites
        itself — it cannot fail the guard and cannot claim anything they haven't written."""
        from wera_agent.nodes.tailor import master_summary

        fallback = master_summary(demo_cv)
        assert fallback.text == demo_cv.summary.text
        assert fallback.source_ids == [demo_cv.summary.id]

    def test_a_cv_with_no_summary_gets_no_invented_one(self, demo_cv):
        """The one case where an empty profile is right: there is nothing honest to fall back to."""
        from wera_agent.nodes.tailor import master_summary

        assert master_summary(demo_cv.model_copy(update={"summary": None})).text == ""


def test_only_lists_skills_from_the_master_cv(pack, demo_cv):
    p, _ = pack
    assert set(p.cv.skills) <= {s.name for s in demo_cv.skills}


def test_reports_gaps_the_cv_cant_back_and_never_invents_them(pack):
    p, _ = pack
    assert [k.term for k in p.ats.keywords if not k.matched and not k.in_master_cv] == ["Docker", "Model evaluation"]
    assert "docker" not in tailored_text(p.cv).lower()
    assert round(p.ats.coverage * 100) == 86


def test_quotes_a_company_fact_verbatim(pack):
    p, seed = pack
    assert len(p.letter.company_fact.quote) > 20
    assert p.letter.company_fact.quote in seed["description"]
    assert p.letter.greeting == "Dear Hiring Team,"


class TestTheLetterReadsLikeAPerson:
    """LTR-04. A letter can be perfectly honest, perfectly cited, and still read like a form —
    which is the one thing a cover letter cannot afford."""

    def test_it_contains_none_of_the_banned_phrases(self, pack):
        from wera_agent.nodes.tailor import banned_phrases

        p, _ = pack
        assert banned_phrases(p.letter) == []

    def test_it_does_not_open_by_announcing_the_application(self, pack):
        """ "I am writing to apply for…" tells the reader what they already know, and spends the
        one sentence most likely to be read on nothing."""
        p, _ = pack
        opening = p.letter.paragraphs[0].text.lower()
        assert not opening.startswith("i'm applying")
        assert "i am writing" not in opening

    def test_every_demo_letter_is_in_the_ltr_01_band(self, api):
        """LTR-01: 250–350 words. Every demo letter was 229–236 — short because the writer used
        two bullets per role and stopped, not because there was nothing left to say.

        Asserted over the packs the demo actually produces, not over `generate_pack` on arbitrary
        jobs: a job the CV barely matches has little to say, and padding it to a word count is the
        exact filler LTR-04 exists to remove.
        """
        for item in api.ok("GET", "/applications")["items"]:
            app = api.ok("GET", f"/applications/{item['id']}")
            letter = (app.get("pack") or {}).get("letter")
            if letter:
                assert 250 <= letter["wordCount"] <= 350, f"{app['job']['company']}: {letter['wordCount']} words"
                assert letter["guard"]["pass"], f"{app['job']['company']}: lengthening broke the guard"

    def test_the_ban_list_actually_catches_filler(self, pack):
        """Guards the guard: a check that never fires is indistinguishable from no check. The
        demo letter used to end with one of these, and this is what found it."""
        from wera_agent.nodes.tailor import banned_phrases
        from wera_agent.schemas.pack import Cited

        p, _ = pack
        spoiled = p.letter.model_copy(
            update={"paragraphs": [*p.letter.paragraphs, Cited(text="I am passionate about this perfect fit.", source_ids=[])]}
        )
        assert sorted(banned_phrases(spoiled)) == ["I am passionate about", "perfect fit"]


class TestWhyThisCompany:
    """LTR-06: the paragraph that shows the applicant read the post, not the job title."""

    def test_a_policy_fact_is_quoted_verbatim_from_the_post(self, pack):
        p, seed = pack
        policy = p.letter.company_fact.policy_quote
        if policy:  # the demo post may or may not state one
            assert policy in seed["description"]

    def test_a_post_that_states_no_policy_gets_no_invented_one(self):
        """An absent paragraph beats a fabricated policy every time — this is the half of a
        letter with no CV behind it, so the post is the only thing holding it to the truth."""
        from wera_agent.llm.demo import policy_fact

        assert policy_fact("We build things.\n\n## About\nA company.") == ""
        assert policy_fact(None) == ""

    def test_a_policy_sentence_is_found_and_is_quotable(self):
        from wera_agent.llm.demo import policy_fact

        post = "## About us\nWe make tools.\n\n- We are remote-first across the EU. Everyone gets a learning budget.\n"
        found = policy_fact(post)
        assert found == "We are remote-first across the EU."
        assert found in post  # LTR-02's rule applies here too: verbatim or it doesn't count


def test_never_drafts_sensitive_answers(pack):
    p, _ = pack
    sensitive = [a for a in p.answers if a.sensitive]
    assert [a.question for a in sensitive] == [
        "Are you legally authorised to work in the United Kingdom?",
        "Will you now or in the future require visa sponsorship?",
        "What are your salary expectations?",
    ]
    assert all(a.answer is None for a in sensitive)


def test_records_omitted_lines_so_they_can_be_added_back(pack, demo_cv):
    p, _ = pack
    every = sorted(b.id for r in [*demo_cv.experience, *demo_cv.projects] for b in r.bullets)
    used = [sid for s in p.cv.sections for i in s.items for b in i.bullets for sid in b.source_ids]
    assert sorted(used + p.cv.omitted_bullet_ids) == every


def test_uses_skill_aliases_when_matching(demo_cv):
    assert "Postgres" in skill_text(next(s for s in demo_cv.skills if s.name == "PostgreSQL"))
