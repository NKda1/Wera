"""M6 (20 Nov): an agency's repost is the employer's job, and a location or company reads as one.

With the 19 Nov agency caps gone (eFinancialCareers is a main board for finance), a repost of the
*same* job has to be caught as a duplicate: eFinancialCareers' "Investment Banking Analyst, M&A -
Citi" is Citi's "Investment Banking Analyst, M&A". And a source's placeholder ("Hybrid", "Not
stated") is no city, and a company in capitals is shown in ordinary case.
"""

from wera_agent.clock import iso, now_ms
from wera_agent.domain import dedupe
from wera_agent.schemas.jobs import Job, JobSource, Location
from wera_agent.sources import normalise

NOW = now_ms()


def job(job_id: str, url: str, title: str, company: str, city: str = "London") -> Job:
    at = iso(NOW)
    return Job.model_validate(
        {
            "id": job_id, "dedupe_key": dedupe.dedupe_key(ats=None, apply_url=url, company=company, title=title, city=city),
            "title": title, "company": company, "location": Location(city=city, country="GB", raw=city), "remote": None,
            "description_md": "Advise clients on mergers and acquisitions. " * 10, "description_status": "full",
            "apply_url": url, "sources": [JobSource(channel="board_search", detail=dedupe._host(url), seen_at=at)],
            "language": "en", "first_seen": at, "last_seen": at, "status": "new",
        }
    )  # fmt: skip


CITI = "https://citi.wd5.myworkdayjobs.com/2026CampusExternal/job/London/Investment-Banking-Analyst_25888"
REED = "https://www.reed.co.uk/jobs/investment-banking-analyst/56000001"


class TestTheEmployerInTheTitle:
    def test_an_agency_names_the_employer_at_the_end(self):
        assert dedupe.employer_named("eFinancialCareers", "Investment Banking Analyst, M&A - Citi") == "Citi"
        assert (
            dedupe.bare_title("eFinancialCareers", "Investment Banking Analyst, M&A - Citi") == "Investment Banking Analyst, M&A"
        )
        assert dedupe.employer_named("eFinancialCareers", "Quantitative Research Analyst - PIMCO") == "PIMCO"

    def test_a_tail_about_the_job_names_no_one(self):
        assert dedupe.employer_named("Hays", "Analyst - Summer 2027") is None
        assert dedupe.employer_named("Reed", "Graduate Engineer - London") is None

    def test_an_employer_naming_a_team_is_not_an_agency_naming_an_employer(self):
        assert dedupe.employer_named("Barings", "Analyst Programme - Capital Solutions") is None
        assert dedupe.employer_key("Barings", "Analyst Programme - Capital Solutions - Barings") == "barings"


class TestAnAgencyRepostIsADuplicate:
    def test_it_is_the_employers_job(self):
        repost = job("a", REED, "Investment Banking Analyst, M&A - Citi", "eFinancialCareers")
        original = job("b", CITI, "Investment Banking Analyst, M&A", "Citi")
        assert dedupe.is_near_duplicate(repost, original)
        assert dedupe.is_copy_elsewhere(repost, original)

    def test_another_of_the_agencys_jobs_is_not(self):
        repost = job("a", REED, "Investment Banking Analyst, Consumer & Retail - Citi", "eFinancialCareers")
        original = job("b", CITI, "Investment Banking Analyst, M&A", "Citi")
        assert not dedupe.is_copy_elsewhere(repost, original)

    def test_saved_once_with_both_sources(self, store):
        original = job("job-citi", CITI, "Investment Banking Analyst, M&A", "Citi")
        store.upsert_jobs([original])
        stored, new = store.upsert_jobs([job("job-efc", REED, "Investment Banking Analyst, M&A - Citi", "eFinancialCareers")])
        assert new == set() and stored[0].id == "job-citi"
        assert {s.detail for s in stored[0].sources} == {"citi.wd5.myworkdayjobs.com", "reed.co.uk"}

    def test_the_apis_copies_are_one_lead(self):
        assert dedupe.same_posting_key("eFinancialCareers", "Investment Banking Analyst, M&A - Citi") == dedupe.same_posting_key(
            "Citi", "Investment Banking Analyst, M&A"
        )


class TestWhereAndWho:
    def test_placeholders_are_no_city(self):
        for words in ("Not stated", "Hybrid", "Remote", "London or Manchester", "Multiple locations"):
            assert normalise.city_of(words, "GB") is None, words
        assert normalise.city_of("london", "GB") == "London"
        assert normalise.city_of("Oregon", "US") == "Oregon"

    def test_a_saved_job_keeps_the_words_but_files_no_city(self):
        saved = normalise.from_pasted("x" * 300, title="Analyst", company="Barings", location_raw="Hybrid", now=NOW)
        assert (saved.location.city, saved.location.raw) == (None, "Hybrid")

    def test_a_company_in_capitals_reads_in_ordinary_case(self):
        made = normalise.from_pasted(
            "x" * 300, title="Analyst", company="NATIONAL BANK OF CANADA", location_raw="Toronto", now=NOW
        )
        assert made.company == "National Bank of Canada"
        for brand in ("AECOM", "CITI"):
            assert normalise.from_pasted("x" * 300, title="Analyst", company=brand, location_raw="", now=NOW).company == brand
        # The key is the company as written, lower-cased: an old sighting still finds its job.
        assert (
            made.dedupe_key
            == normalise.from_pasted(
                "x" * 300, title="Analyst", company="National Bank of Canada", location_raw="Toronto", now=NOW
            ).dedupe_key
        )

    def test_a_boards_html_entities_are_characters(self):
        made = normalise.from_pasted(
            "x" * 300, title="Analyst &amp; Associate", company="Wells &amp; Co.", location_raw="", now=NOW
        )
        assert (made.title, made.company) == ("Analyst & Associate", "Wells & Co.")


class TestShownUnderTheEmployer:
    """20 Nov: with no limit on any one site, 14 of a search's 15 jobs were eFinancialCareers'.
    They were fourteen banks' jobs; each now reads under its bank, with the agency beside it."""

    CARD = {
        "title": "Investment Banking Analyst, M&A - Citi",
        "company": "eFinancialCareers",
        "location": "London",
        "description": "x" * 400,
    }

    def test_a_repost_is_the_employers_job_posted_by_the_agency(self):
        saved = normalise.from_scrape(
            dict(self.CARD),
            url="https://www.efinancialcareers.co.uk/jobs-1",
            site="efinancialcareers.co.uk",
            markdown=None,
            now=now_ms(),
        )
        assert (saved.company, saved.title, saved.posted_by) == ("Citi", "Investment Banking Analyst, M&A", "eFinancialCareers")

    def test_an_employers_own_job_names_no_one_else(self):
        card = dict(self.CARD, title="Investment Banking Analyst, M&A", company="Citi")
        saved = normalise.from_scrape(card, url="https://jobs.citi.com/1", site="jobs.citi.com", markdown=None, now=now_ms())
        assert saved.posted_by is None and saved.company == "Citi"

    def test_saved_reposts_can_be_put_under_their_employer(self, store):
        from wera_agent.services.store import save_job

        repost = job("job-efc-2", REED, "Investment Banking Analyst, M&A - Citi", "eFinancialCareers")
        save_job(store.s, repost.model_copy(update={"company": "eFinancialCareers"}), questions=[])
        assert store.name_employers(apply=False)[("eFinancialCareers", "Citi")] == 1
        store.name_employers(apply=True)
        saved = store._job(store._job_row("job-efc-2"))
        assert (saved.company, saved.posted_by, saved.title) == ("Citi", "eFinancialCareers", "Investment Banking Analyst, M&A")


class TestNamesThe20NovRunMissed:
    """20 Nov, live: "Financial Analyst - Educate2trade Ltd" stayed under eFinancialCareers (a digit
    in a name read as a year), and "… Investment Analyst - eFinancialCareers" kept the agency's name."""

    def test_a_digit_inside_a_name_is_still_a_name(self):
        assert dedupe.employer_named("eFinancialCareers", "Financial Analyst - Educate2trade Ltd") == "Educate2trade Ltd"
        assert dedupe.employer_named("eFinancialCareers", "Analyst - Summer 2027") is None
        assert dedupe.employer_named("eFinancialCareers", "Analyst - 3 days in office") is None

    def test_the_posters_own_name_comes_off_the_title(self):
        card = {
            "title": "Global Equities ESG Investment Analyst - eFinancialCareers",
            "company": "eFinancialCareers",
            "location": "London",
            "description": "x" * 400,
        }
        saved = normalise.from_scrape(
            card, url="https://www.efinancialcareers.co.uk/jobs-2", site="efinancialcareers.co.uk", markdown=None, now=now_ms()
        )
        assert saved.title == "Global Equities ESG Investment Analyst" and saved.posted_by is None
