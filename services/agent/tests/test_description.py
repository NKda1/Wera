"""18 Nov: a job description laid out to read, and summarised on click.

The furniture below is what the 2,067 stored jobs used (ATS feeds, boards, job APIs). WERA is
for every kind of job, not the software posts it is mostly tested on, so half the posts here are
a nurse's, a solicitor's, a teacher's and a store manager's.
"""

import pytest

from wera_agent.domain.description import VERSION, describe
from wera_agent.domain.verbatim import supported
from wera_agent.schemas.llm_outputs import JobSummaryOutput

NURSE = """Band: 5
Contract: Permanent
Working pattern: Full time, including nights and weekends
Closing date: 12 December 2026

Job overview
We are looking for a compassionate Staff Nurse to join our busy acute medical ward at St Mary's Hospital.

Main duties of the job
- Deliver high-quality, person-centred nursing care
- Administer medicines safely in line with NMC guidance
- Supervise and support student nurses and healthcare assistants

Person specification
Essential
- Registered Nurse (Adult) with current NMC registration
- Evidence of continuing professional development
Desirable
- Experience in acute medicine

About us
St Mary's is one of the largest teaching trusts in the country, serving 1.2 million people.

This post is subject to an Enhanced DBS check. We welcome applications from people of all backgrounds and are committed to equal opportunities.
"""

SOLICITOR = """ABOUT HARGREAVE & COLE
Hargreave & Cole is a top-50 UK law firm with offices in Leeds and Manchester.

THE ROLE
As a Trainee Solicitor you will complete four six-month seats across our commercial, litigation, property and private client teams.

WHAT WE'RE LOOKING FOR
- A 2:1 degree in any discipline
- Completion of the SQE or LPC by September 2027
- Excellent written and verbal communication

WHAT WE OFFER
- Salary of £32,000 rising to £36,000 in year two
- SQE fees paid
- 25 days' holiday

Hargreave & Cole is an equal opportunity employer and does not discriminate on the basis of any protected characteristic.
"""

TEACHER = """Oakfield Primary School is a friendly, two-form entry school in Bristol rated Good by Ofsted.
We are seeking an enthusiastic Year 4 class teacher to join us from January 2027.

The successful candidate will:
- Plan and teach engaging lessons across the primary curriculum
- Track pupil progress and report to parents

Requirements:
- Qualified Teacher Status (QTS)
- Experience teaching Key Stage 2

This is a fixed-term contract for 12 months, maternity cover. Oakfield is committed to safeguarding; the successful applicant will need an Enhanced DBS check.
"""

RETAIL = """Store Manager — Northgate Retail
Salary: £38,000 + bonus

Responsibilities- Lead a team of 30 colleagues across sales and stockroom
- Hit weekly sales and margin targets
- Recruit, train and develop your team

Requirements- 3+ years of retail management experience
- Weekend working on a rota

Benefits- 30% colleague discount
- Pension scheme
"""

LEVER_STYLE = """Acme Robotics builds warehouse robots used by retailers in 20 countries.

As a Field Service Engineer you will keep our customers' fleets running.

What You'll Own
Install and commission robots at customer sites.

Diagnose and repair mechanical and electrical faults.

Train customer staff to run the fleet safely.

What You'll Need
A HNC or HND in an engineering discipline.

Willingness to travel up to 60% of the time.

Full UK driving licence.
"""


def sections(view):
    return [(s.kind, s.heading, s.fold) for s in view.sections]


def facts(view):
    return {f.label: f.value for f in view.facts}


class TestLayout:
    def test_a_nursing_post(self):
        view = describe(NURSE, title="Staff Nurse", company="St Mary's Hospital")
        assert facts(view) == {
            "Band": "5",
            "Contract": "Permanent",
            "Working pattern": "Full time, including nights and weekends",
            "Closing date": "12 December 2026",
            "Vetting": "Enhanced DBS check",
        }
        kinds = sections(view)
        assert ("role", "Job overview", False) in kinds and ("role", "Main duties of the job", False) in kinds
        assert ("requirements", "Person specification", False) in kinds
        spec = next(s for s in view.sections if s.heading == "Person specification")
        assert [(b.t, b.text) for b in spec.blocks if b.t == "h"] == [("h", "Essential")]
        assert ("nice", "Desirable", False) in kinds  # its own section, so it reads as preferred
        assert ("company", "About us", True) in kinds
        # The equal-opportunities paragraph is folded into the small print, and the DBS check in
        # it still surfaces as a fact.
        assert kinds[-1] == ("legal", "The small print", True)

    def test_a_law_firm_in_capitals_keeps_its_name_and_folds_its_blurb(self):
        view = describe(SOLICITOR, title="Trainee Solicitor", company="Hargreave & Cole")
        assert sections(view) == [
            ("company", "About Hargreave & Cole", True),
            ("role", "The role", False),
            ("requirements", "What we're looking for", False),
            ("benefits", "What we offer", False),
            ("legal", "The small print", True),
        ]
        assert view.sections[2].blocks[0].items == [
            "A 2:1 degree in any discipline",
            "Completion of the SQE or LPC by September 2027",
            "Excellent written and verbal communication",
        ]

    def test_a_school_post_with_colon_headings_and_prose_facts(self):
        view = describe(TEACHER, title="Year 4 Teacher", company="Oakfield Primary School")
        assert facts(view)["Contract"] == "Fixed-term contract for 12 months"
        assert facts(view)["Vetting"] == "Enhanced DBS check"
        # The opening is the school's blurb, then the job ("We are seeking…"), which stays shown.
        assert [s.kind for s in view.sections][:4] == ["company", "intro", "role", "requirements"]

    def test_headings_glued_to_their_first_item(self):
        view = describe(RETAIL, title="Store Manager", company="Northgate Retail")
        assert facts(view)["Salary"] == "£38,000 + bonus"
        assert facts(view)["Experience"] == "3+ years of retail management experience"
        role = next(s for s in view.sections if s.kind == "role")
        assert (
            role.heading == "Responsibilities"
            and role.blocks[0].items[0] == "Lead a team of 30 colleagues across sales and stockroom"
        )
        assert len(role.blocks[0].items) == 3

    def test_one_paragraph_per_item_is_a_list(self):
        view = describe(LEVER_STYLE, title="Field Service Engineer", company="Acme Robotics")
        assert sections(view)[:3] == [
            ("company", "About Acme Robotics", True),
            ("intro", None, False),
            ("role", "What You'll Own", False),
        ]
        assert view.sections[2].blocks[0].t == "ul" and len(view.sections[2].blocks[0].items) == 3
        assert next(s for s in view.sections if s.kind == "requirements").blocks[0].t == "ul"

    def test_a_sentence_with_a_dash_is_not_a_heading(self):
        view = describe(
            "We hire outliers - people who have achieved difficult things.\n\nAbout the role\nYou will lead.",
            title="",
            company="",
        )
        assert view.sections[0].blocks[0].text == "We hire outliers - people who have achieved difficult things."

    def test_a_short_line_over_a_list_is_a_sub_heading(self):
        view = describe("What you'll do\n\nCapital strategy\n- Raise debt\n- Model scenarios\n\nExecution\n- Close deals")
        blocks = view.sections[0].blocks
        assert [(b.t, b.text) for b in blocks if b.t == "h"] == [("h", "Capital strategy"), ("h", "Execution")]

    def test_visa_facts_are_whole_sentences_about_visas(self):
        post = "Great role.\nYou must be eligible for export licences without sponsorship for an export license.\nVisa sponsorship is not available for this position."
        assert facts(describe(post))["Visa"] == "Visa sponsorship is not available for this position."
        assert "Visa" not in facts(describe("No sponsorship for an export license is needed."))

    def test_every_word_shown_is_the_employers(self):
        """Laid out, never rewritten: each shown line is in the post as written."""
        for post in (NURSE, SOLICITOR, TEACHER, RETAIL, LEVER_STYLE):
            view = describe(post)
            for s in view.sections:
                for b in s.blocks:
                    for line in [b.text] if b.text else b.items or []:
                        assert line in " ".join(post.split()) or line in post, line

    def test_nothing_to_lay_out(self):
        assert describe(None) is None and describe("   ") is None
        assert describe("Just one paragraph about the job.").version == VERSION


class TestHeldToThePost:
    POST = "Lead a team of 30 colleagues. You need 3+ years of retail management experience. 30% colleague discount."

    def test_numbers_must_be_in_the_post(self):
        assert supported("3+ years of retail management experience", self.POST)
        assert not supported("5+ years of retail management experience", self.POST)

    def test_most_words_must_be_in_the_post(self):
        assert supported("Leading a team of 30 colleagues", self.POST)  # "Leading" meets "Lead"
        assert not supported("On-call rota every other weekend", self.POST)

    def test_a_summary_keeps_only_what_the_post_supports(self):
        out = JobSummaryOutput.model_validate(
            {
                "gist": "Store manager leading 30 colleagues",
                "duties": ["Lead a team of 30 colleagues", "Run the online store's SEO"],
                "mustHaves": ["3+ years of retail management experience", "A degree in marketing"],
                "niceToHaves": [],
                "perks": ["30% colleague discount", "Company car"],
                "watchOuts": ["Night shifts"],
            },
            context={"post": self.POST},
        )
        assert (out.duties, out.must_haves, out.perks, out.watch_outs) == (
            ["Lead a team of 30 colleagues"],
            ["3+ years of retail management experience"],
            ["30% colleague discount"],
            [],
        )


# ---- through the API ----


def a_job(api) -> dict:
    return next(j for j in api.ok("GET", "/jobs")["items"] if j.get("descriptionMd"))


class TestTheCard:
    def test_every_listed_job_carries_its_layout(self, api):
        for job in api.ok("GET", "/jobs")["items"]:
            if job.get("descriptionMd"):
                assert job["descriptionView"]["version"] == VERSION
                assert job["descriptionView"]["sections"]

    def test_a_summary_is_written_once_and_kept(self, api, monkeypatch):
        from wera_agent.llm import gateway

        job = a_job(api)
        assert "summary" not in job
        out = api.ok("POST", f"/jobs/{job['id']}/summary")
        summary = out["summary"]
        assert summary["gist"] or summary["duties"] or summary["mustHaves"]
        post = job["descriptionMd"]
        for item in summary["duties"] + summary["mustHaves"] + summary["perks"]:
            assert supported(item, post)
        # The second click is free: nothing calls a model.
        monkeypatch.setattr(gateway, "_structured", lambda *a, **k: pytest.fail("called the model again"))
        assert api.ok("POST", f"/jobs/{job['id']}/summary")["summary"] == summary
        assert api.ok("GET", f"/jobs/{job['id']}")["summary"] == summary

    def test_a_changed_post_hides_its_old_summary(self, api, store):
        from wera_agent.services.store import save_job

        job = a_job(api)
        api.ok("POST", f"/jobs/{job['id']}/summary")
        stored = store._job(store._job_row(job["id"]))
        assert stored.summary is not None
        stored.description_md = (stored.description_md or "") + "\n\nThe role now includes weekend on-call."
        save_job(store.s, stored)
        store.s.commit()
        assert "summary" not in api.ok("GET", f"/jobs/{job['id']}")

    def test_a_fuller_description_is_laid_out_again(self, api, store):
        from wera_agent.services.store import save_job

        job = a_job(api)
        stored = store._job(store._job_row(job["id"]))
        stored.description_md = (stored.description_md or "") + "\n\nPerks\n- Four-day week\n- Free lunch"
        save_job(store.s, stored)
        store.s.commit()
        view = api.ok("GET", f"/jobs/{job['id']}")["descriptionView"]
        assert ["Four-day week", "Free lunch"] in [b.get("items") for s in view["sections"] for b in s["blocks"]]

    def test_the_budget_is_checked_first(self, api, monkeypatch):
        from wera_agent.services import store as store_module

        job = a_job(api)
        monkeypatch.setattr(
            store_module.Store,
            "_assert_budget",
            lambda self: (_ for _ in ()).throw(store_module.ApiError(402, "BUDGET_REACHED", "spent")),
        )
        assert api.code("POST", f"/jobs/{job['id']}/summary") == "BUDGET_REACHED"

    def test_reformat_stores_every_view(self, client):
        from wera_agent.db.models import JobRow
        from wera_agent.db.session import transaction
        from wera_agent.services.store import Store

        with transaction() as session:
            for row in session.query(JobRow).all():
                row.data = {k: v for k, v in row.data.items() if k != "descriptionView"}
        with transaction() as session:
            changed, total = Store(session).reformat_saved()
        assert changed > 0 and total >= changed
        with transaction() as session:
            assert Store(session).reformat_saved()[0] == 0
