"""18 Nov: learn about the company from its post and its own About page, and use it in the letter.

The rules being tested are the ones that keep this honest: only the employer's own site, never an
agency's, never LinkedIn; one page, after robots.txt; every item quoted and checked; and a site
fact reaches a letter only word for word from the page WERA stored (LTR-02).
"""

import httpx
import pytest
import respx

from wera_agent.domain import dedupe
from wera_agent.llm.demo import PackInput
from wera_agent.schemas.jobs import Job, JobSource, Location
from wera_agent.schemas.llm_outputs import CompanyBriefOutput
from wera_agent.schemas.pack import BriefItem, CompanyBrief, CompanySource
from wera_agent.services.demo_data import seed_to_job
from wera_agent.sources import company_page

ABOUT = """<html><head><script>track()</script><title>About</title></head><body>
<header><nav>Home · Services · Careers · Contact</nav></header>
<main>
<h1>About Kew Vale Care</h1>
<p>Kew Vale Care runs eight residential care homes across Surrey for older people living with dementia.</p>
<p>Founded in 1998 as a family business, we now employ 640 carers and nurses.</p>
<p>We believe everyone deserves to be known by name, and we train every carer in person-centred dementia care.</p>
</main>
<footer>© Kew Vale Care · Privacy · Cookies</footer>
</body></html>"""


def a_job(company: str = "Kew Vale Care", apply_url: str | None = "https://careers.kewvale.co.uk/jobs/42") -> Job:
    return Job(
        id="job-kv",
        dedupe_key="kv",
        title="Senior Care Assistant",
        company=company,
        location=Location(raw="Guildford"),
        remote="onsite",
        description_md="Join our dementia care team in Guildford. You will lead a shift of four carers.",
        description_status="full",
        apply_url=apply_url,
        sources=[JobSource(channel="manual", detail="pasted", seen_at="2026-11-18T00:00:00.000Z")],
        language="en",
        first_seen="2026-11-18T00:00:00.000Z",
        last_seen="2026-11-18T00:00:00.000Z",
        status="new",
    )


class TestFindingTheSite:
    def test_an_agencys_post_has_no_employer_to_read(self):
        for company in ("eFinancialCareers", "Spectrum IT Recruitment", "Hays", "Acme Staffing Ltd"):
            assert dedupe.is_agency(company), company
        assert not dedupe.is_agency("Kew Vale Care")

    def test_the_apply_link_names_the_site_and_a_careers_subdomain_is_dropped(self):
        assert company_page.site_from_links(a_job()) == "https://kewvale.co.uk"

    def test_a_board_an_ats_or_linkedin_is_not_the_company(self):
        for url in (
            "https://boards.greenhouse.io/kewvale/jobs/1",
            "https://www.linkedin.com/jobs/view/1",
            "https://www.adzuna.co.uk/jobs/1",
        ):
            assert company_page.site_from_links(a_job(apply_url=url)) is None, url


@pytest.mark.usefixtures("outbound")
class TestReadingThePage:
    @respx.mock
    def test_the_about_page_is_read_after_robots_and_without_its_furniture(self):
        respx.get("https://kewvale.co.uk/robots.txt").mock(
            return_value=httpx.Response(200, text="User-agent: *\nDisallow: /admin\n")
        )
        respx.get("https://kewvale.co.uk/about").mock(
            return_value=httpx.Response(200, text=ABOUT, headers={"content-type": "text/html"})
        )
        page, note = company_page.read(a_job())
        assert note is None and page is not None
        assert page.url == "https://kewvale.co.uk/about"
        assert "eight residential care homes" in page.text and "640 carers" in page.text
        for furniture in ("track()", "Home · Services", "Cookies"):
            assert furniture not in page.text

    @respx.mock
    def test_a_site_that_says_no_is_not_read(self):
        respx.get("https://kewvale.co.uk/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
        about = respx.get("https://kewvale.co.uk/about")
        page, note = company_page.read(a_job())
        assert page is None and "robots.txt" in (note or "") and not about.called

    @respx.mock
    def test_a_redirect_off_the_site_is_not_followed(self):
        respx.get("https://kewvale.co.uk/robots.txt").mock(return_value=httpx.Response(200, text=""))
        for path in ("/about", "/about-us", "/"):
            respx.get(f"https://kewvale.co.uk{path}").mock(
                return_value=httpx.Response(302, headers={"location": "https://elsewhere.example/"})
            )
        elsewhere = respx.get("https://elsewhere.example/")
        page, note = company_page.read(a_job())
        assert page is None and not elsewhere.called and "couldn't find a page" in (note or "")

    def test_an_agencys_post_reads_nothing(self):
        page, note = company_page.read(a_job(company="Hays", apply_url="https://hays.co.uk/job/1"))
        assert page is None and "agency" in (note or "")

    def test_no_site_and_no_search_key_says_so(self):
        page, note = company_page.read(a_job(apply_url="https://boards.greenhouse.io/kewvale/jobs/1"))
        assert page is None and "couldn't find Kew Vale Care's own website" in (note or "")


class TestTheBriefIsHeldToItsSources:
    POST = "Join our dementia care team in Guildford. You will lead a shift of four carers."
    SITE = "Kew Vale Care runs eight residential care homes across Surrey. Founded in 1998, we now employ 640 carers and nurses."

    def brief(self, items):
        return CompanyBriefOutput.model_validate({"items": items}, context={"post": self.POST, "site": self.SITE})

    def test_a_quote_must_be_in_the_source_it_names(self):
        out = self.brief(
            [
                {
                    "topic": "what_they_do",
                    "text": "Runs eight care homes in Surrey",
                    "source": "site",
                    "quote": "runs eight residential care homes across Surrey",
                },
                {
                    "topic": "size_or_stage",
                    "text": "Employs 640 carers",
                    "source": "post",
                    "quote": "we now employ 640 carers and nurses",
                },
                {
                    "topic": "values",
                    "text": "Rated Outstanding by the CQC",
                    "source": "site",
                    "quote": "Rated Outstanding by the CQC",
                },
            ]
        )
        assert [(i.topic, i.source) for i in out.items] == [("what_they_do", "site")]

    def test_an_ellipsis_marks_words_left_out(self):
        from wera_agent.domain.verbatim import quoted_in

        assert quoted_in("...runs eight residential care homes…", self.SITE)
        assert quoted_in("Kew Vale Care runs eight … homes across Surrey", self.SITE)
        assert not quoted_in("Surrey … Kew Vale Care runs", self.SITE)  # out of order
        assert not quoted_in("runs eight … hospitals", self.SITE)

    def test_the_text_may_not_say_more_than_its_quote(self):
        out = self.brief(
            [
                {
                    "topic": "size_or_stage",
                    "text": "Employs 900 staff in 12 countries",
                    "source": "site",
                    "quote": "we now employ 640 carers and nurses",
                }
            ]
        )
        assert out.items == []

    def test_no_site_means_no_site_items(self):
        out = CompanyBriefOutput.model_validate(
            {
                "items": [
                    {
                        "topic": "what_they_do",
                        "text": "Runs care homes",
                        "source": "site",
                        "quote": "runs eight residential care homes",
                    }
                ]
            },
            context={"post": self.POST, "site": ""},
        )
        assert out.items == []


SITE_TEXT = "Fernhill AI builds guardrails for language models. We are a team of 40 based in London and Lisbon."


def with_brief(inp: PackInput) -> PackInput:
    inp.brief = CompanyBrief(
        company=inp.job.company,
        items=[
            BriefItem(
                topic="size_or_stage",
                text="A team of 40 in London and Lisbon",
                source="site",
                quote="a team of 40 based in London and Lisbon",
            )
        ],
        site=CompanySource(
            url="https://fernhill.example/about", fetched_at="2026-11-18T00:00:00.000Z", text_hash="h", text=SITE_TEXT
        ),
        model="m",
        created_at="2026-11-18T00:00:00.000Z",
    )
    return inp


class TestTheLetter:
    def test_the_brief_reaches_the_letter_as_verified_facts(self, demo_cv, demo_jobs):
        from wera_agent.nodes.letter_plan import context_text

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][0], 0)
        text = context_text(
            None, with_brief(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1)).brief
        )
        assert "https://fernhill.example/about" in text and '"a team of 40 based in London and Lisbon"' in text

    @pytest.mark.parametrize(
        "quoted,kept", [("We are a team of 40 based in London and Lisbon", True), ("We are a team of 400 worldwide", False)]
    )
    def test_a_site_quote_counts_only_word_for_word(self, demo_cv, demo_jobs, store, quoted, kept):
        from wera_agent.nodes import tailor
        from wera_agent.schemas.llm_outputs import LetterOutput, LetterParagraph

        job, evidence, questions = seed_to_job(demo_jobs["shortlist"][0], 0)
        inp = with_brief(PackInput(job=job, evidence=evidence, questions=questions, cv=demo_cv, cv_pages=1))
        out = LetterOutput.model_construct(
            greeting="Dear Hiring Team,",
            paragraphs=[
                LetterParagraph.model_construct(text=f"Paragraph {i}.", source_ids=[], section=s, theme="")
                for i, s in enumerate(["opening_hook", "strategic_alignment_and_culture", "call_to_action_and_close"])
            ],
            signoff="Best regards,\nJordan Ellery",
            hook_type="Introduction",
            company_quote="",
            policy_quote="",
            site_quote=quoted,
        )
        letter = tailor._guard_letter(store.s, out, inp)
        assert letter.company_fact.site_quote == (quoted if kept else "")


# ---- through the API (demo mode: no web pages, so post-only briefs) ----


def ready(api) -> dict:
    return next(a for a in api.ok("GET", "/applications")["items"] if a["status"] == "ready_for_review")


class TestOnTheReviewScreen:
    def test_a_brief_in_demo_mode_is_from_the_post_and_says_why(self, api):
        app = ready(api)
        out = api.ok("POST", f"/applications/{app['id']}/company-brief")
        brief = out["pack"]["company"]
        assert brief["company"] == out["job"]["company"]
        assert "Demo mode reads no web pages" in brief["siteNote"] and "site" not in brief
        post = api.ok("GET", f"/jobs/{out['jobId']}")["descriptionMd"]
        assert brief["items"] and all(i["source"] == "post" and i["quote"] in post for i in brief["items"])
        assert api.ok("GET", f"/applications/{app['id']}")["pack"]["company"] == brief

    def test_the_letter_is_rewritten_alone(self, api, drafted):
        app = ready(api)
        before = api.ok("GET", f"/applications/{app['id']}")["pack"]
        api.ok("POST", f"/applications/{app['id']}/company-brief")
        nxt = api.ok("POST", f"/applications/{app['id']}/letter/rewrite")
        assert nxt["status"] == "drafting"
        drafted()
        after = api.ok("GET", f"/applications/{app['id']}")
        assert after["status"] == "ready_for_review"
        assert after["pack"]["cv"] == before["cv"] and after["pack"]["company"]
        assert next(c for c in after["pack"]["ats"]["formatChecks"] if c["id"] == "pages")["message"] == "1 page"

    def test_a_rewrite_needs_a_pack_under_review(self, api):
        app = ready(api)
        api.ok("POST", f"/applications/{app['id']}/approve")
        assert api.code("POST", f"/applications/{app['id']}/letter/rewrite") == "INVALID_STATE"
