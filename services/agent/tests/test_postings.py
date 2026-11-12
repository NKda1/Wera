"""SRCH-21: sorting a URL into posting, listing, closed or other before anything reads it.

Every URL here is a real one from the database as of 11 Nov — the shapes the search actually
returned, including all the pages that were saved as jobs and weren't.
"""

import pytest

from wera_agent.config import sites
from wera_agent.sources.postings import classify, is_closed_page

POSTINGS = [
    # The four boards, by their `posting_pattern`.
    "https://builtinlondon.uk/job/it-systems-ai-engineer/11820879",
    "https://www.welcometothejungle.com/en/companies/marso-robotics/jobs/ai-engineer-h-f_paris",
    "http://www.welcometothejungle.com/en/companies/coface/jobs/ai-engineer_bois-colombes",
    "https://www.welcometothejungle.com/fr/companies/gain/jobs/ai-engineer_amsterdam_w4sdrauv",
    "https://app.welcometothejungle.com/jobs/LkZ4M36n",
    "https://www.gradcracker.com/hub/1341/kingspan/graduate-job/86762/continuous-improvement-graduate-engineer-2027",
    "https://www.brightnetwork.co.uk/graduate-jobs/ge-vernova/graduate-software-engineer-rugby-2026-apqt",
    "https://www.brightnetwork.co.uk/graduate-jobs/mi5-mi6-gchq/software-engineering-apprenticeship-registration-of-interest",
    # Employer job systems, and employers' own pages that embed one.
    "https://job-boards.greenhouse.io/anthropic/jobs/5456619008",
    "https://boards.greenhouse.io/cloudflare/jobs/7695702?gh_jid=7695702",
    "https://job-boards.eu.greenhouse.io/rightmovecareers/jobs/4997188101",
    "https://jobs.lever.co/palantir/10dfc8bc-99ad-4ca2-ab76-853cb90a92c2",
    "https://jobs.ashbyhq.com/airwallex/1d4b22cd-33cd-44b8-83fe-108d11907f2f",
    "https://jobs.ashbyhq.com/partly.com/1bfad1d5-d6b6-4e5a-a8aa-7fae0b5cab49",  # a dotted board token
    "https://databricks.com/company/careers/open-positions/job?gh_jid=8593713002",
    "https://www.pinterestcareers.com/jobs/?gh_jid=6121450",
    "https://coreweave.com/careers/job?4720112006&board=coreweaveu&gh_jid=4720112006",
    # The generic rule: a job segment with an id after it.
    "https://www.wiz.io/careers/job/4621534006/:title?gh_jid=4621534006",
    "https://careers.duolingo.com/jobs/8851677002",
    "https://www.optiver.com/join-us/jobs/8313105002/",
    "https://sumup.com/careers/positions/8789067002",
]

# Every board-search "job" in the database that wasn't one, by what it actually is.
LISTINGS = [
    "https://www.gradcracker.com/search/all-disciplines/engineering-work-placements-internships?duration=Year-long&order=deadlines&page=10",
    "https://www.gradcracker.com/search/aerospace/engineering-graduate-jobs?order=start_date&page=3",
    "https://www.gradcracker.com/search/civil-building/town-planning-real-estate-jobs?order=grade_asc&page=5",
    "https://www.gradcracker.com/search/civil-building/town-planning-real-estate-jobs?order=grade_asc&page=4",
    "https://www.gradcracker.com/search/science/bioengineering-biotechnology-jobs-in-scotland",
    "https://www.gradcracker.com/search/technology-it/graduate-jobs",
    "https://www.gradcracker.com/search/technology-it/graduate-jobs?page=2",
    "https://www.gradcracker.com/search/maths-business/business-graduate-jobs?order=deadlines&page=13",
    "https://www.gradcracker.com/search/technology-it/software-systems-jobs?page=2",
    "https://www.gradcracker.com/search/technology-it/software-systems-jobs?page=5",
    "https://www.brightnetwork.co.uk/graduate-jobs/software-development/in-london/",
    "https://www.brightnetwork.co.uk/graduate-jobs/technology/in-london/",
    "https://builtinlondon.uk/company/braze/jobs",
    "https://builtinlondon.uk/jobs/remote",
    "https://builtinlondon.uk/jobs/search/hybrid",
    "https://builtinlondon.uk/jobs/dev-engineering/search/net",
    "https://builtinlondon.uk/jobs/sales/search/account-executive",
    "https://builtinlondon.uk/jobs/sales/search/account-manager",
    "https://builtinlondon.uk/jobs/finance/search/accountant",
    # A discovered board's category pages, and an employer's whole board.
    "https://www.milkround.com/jobs/software-engineer",
    "https://www.milkround.com/jobs/junior-software-developer/in-london",
    "https://job-boards.greenhouse.io/anthropic",
]

OTHER = [
    "https://www.welcometothejungle.com/en/companies-v1/mpb-1",
    "https://app.welcometothejungle.com/jobs/YT7-vf_x/company",
    "https://app.welcometothejungle.com/jobs/4mXLoBCw/company",
    "https://solutions.welcometothejungle.com/en/blog/employer-branding-strategy-lessons",
    "https://builtinlondon.uk/company/shield-5",
    # Gradcracker's "featured graduate employer" advert slot, which sits on every results page
    # dressed as a posting with job id 1. Seen on the live harvest of 11 Nov.
    "https://www.gradcracker.com/hub/9000001/featured-graduate-employer/graduate-job/1/graduate-software-engineer",
    "https://www.linkedin.com/jobs/view/1234567890",  # SRCH-03: never anything WERA would read
    # An application form, which names its vacancy but carries no posting. Seen on the live run.
    "https://wise.jobs/Workflow?workflowId=e845dd41-c192-48c4-80f2-85eafdf6039b&vacancyId=4404",
    "mailto:jobs@example.com",
]


@pytest.mark.parametrize("url", POSTINGS)
def test_a_posting(url):
    assert classify(url, sites=sites()) == "posting"


@pytest.mark.parametrize("url", LISTINGS)
def test_a_listing_is_never_a_posting(url):
    assert classify(url, sites=sites()) == "listing"


@pytest.mark.parametrize("url", OTHER)
def test_other_pages(url):
    assert classify(url, sites=sites()) == "other"


class TestClosed:
    @pytest.mark.parametrize("title", ["Not Found", "Job Posting Not Found", "404 - Page not found", "This vacancy has expired"])
    def test_a_title_that_says_the_page_is_gone(self, title):
        # Both were saved as jobs from Gradcracker results pages that had since 404ed.
        url = "https://www.gradcracker.com/hub/1341/kingspan/graduate-job/86762/continuous-improvement-graduate-engineer-2027"
        assert classify(url, title=title, sites=sites()) == "closed"

    def test_the_path_can_say_so_too(self):
        assert classify("https://careers.example.com/jobs/12345678/expired", sites=sites()) == "closed"

    def test_an_ordinary_title_says_nothing(self):
        url = "https://builtinlondon.uk/job/it-systems-ai-engineer/11820879"
        assert classify(url, title="IT Systems & AI Engineer", sites=sites()) == "posting"

    def test_a_read_page_closed_in_its_banner(self):
        assert is_closed_page("Data Engineer", "This job is closed. Browse similar roles below.")
        assert not is_closed_page("Data Engineer", "You will build pipelines. " * 80 + "this job is closed")


class TestTheGenericRule:
    """A host WERA has no pattern for: an id makes a posting, a slug alone does not."""

    def test_an_id_after_a_job_segment(self):
        assert classify("https://careers.example.com/jobs/senior-engineer-1234567", sites=[]) == "posting"

    def test_a_slug_alone_is_a_category(self):
        assert classify("https://careers.example.com/jobs/software-engineer", sites=[]) == "listing"

    def test_a_year_is_not_an_id(self):
        assert classify("https://careers.example.com/blog/graduate-roles-2026", sites=[]) == "other"

    def test_a_search_page(self):
        assert classify("https://careers.example.com/search?q=engineer", sites=[]) == "listing"

    def test_a_board_pattern_wins_over_the_generic_rule(self):
        # Built In's postings have no "jobs" segment; its category pages do.
        assert classify("https://builtinlondon.uk/job/full-stack-ai-engineer/11843642", sites=sites()) == "posting"
        assert classify("https://builtinlondon.uk/jobs/remote", sites=sites()) == "listing"
