"""SRCH-05: reading a job off a page without paying Firecrawl for it.

The ladder in `sources.extract` replaced a 5-credit JSON scrape per page, which was 5 of every 7
credits a run spent. These tests cover the rungs that cost nothing, because those are the ones
that have to be right — a rung that silently stops answering doesn't fail, it just gets expensive.
"""

import httpx
import pytest
import respx

from wera_agent.sources import extract

JSON_LD = """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"BreadcrumbList","itemListElement":[]},
 {"@type":"JobPosting","title":"Graduate Software Engineer",
  "hiringOrganization":{"@type":"Organization","name":"Fernhill AI"},
  "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
    "addressLocality":"London","addressCountry":{"name":"United Kingdom"}}},
  "datePosted":"2026-10-20",
  "description":"<p>We build <b>guardrails</b>.</p><ul><li>Python</li><li>FastAPI</li></ul>"}]}
</script></head><body></body></html>"""


class TestJsonLd:
    """The rung that does the work. Google for Jobs is populated by Google reading this markup
    off employers' pages, so every board that wants to appear there publishes it."""

    def test_reads_a_posting_into_a_card(self):
        card = extract._from_json_ld(JSON_LD)
        assert card == {
            "title": "Graduate Software Engineer",
            "company": "Fernhill AI",
            "location": "London, United Kingdom",
            "remote": "",
            "postedAt": "2026-10-20",
            "description": "We build guardrails.\n- Python\n- FastAPI",
        }

    @pytest.mark.parametrize(
        "payload",
        [
            # On its own.
            '{"@type":"JobPosting","title":"T","hiringOrganization":{"name":"C"}}',
            # In a bare list, which is how several boards ship two postings on one page.
            '[{"@type":"Organization","name":"X"},{"@type":"JobPosting","title":"T","hiringOrganization":{"name":"C"}}]',
            # In an @graph, which is what a CMS plugin emits.
            '{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},'
            '{"@type":"JobPosting","title":"T","hiringOrganization":{"name":"C"}}]}',
            # `@type` as a list, which is valid and which a naive equality check would miss.
            '{"@type":["JobPosting","Thing"],"title":"T","hiringOrganization":{"name":"C"}}',
        ],
    )
    def test_finds_a_posting_however_it_is_wrapped(self, payload):
        """All four shapes are valid. Missing any of them would mean paying for a scrape on a
        page that had the answer in plain sight."""
        card = extract._from_json_ld(f'<script type="application/ld+json">{payload}</script>')
        assert card is not None and card["title"] == "T" and card["company"] == "C"

    def test_a_description_arrives_as_text_not_markup(self):
        card = extract._from_json_ld(JSON_LD)
        assert "<p>" not in card["description"] and "<li>" not in card["description"]

    def test_telecommute_is_the_schema_org_way_of_saying_remote(self):
        page = JSON_LD.replace('"datePosted"', '"jobLocationType":"TELECOMMUTE","datePosted"')
        assert extract._from_json_ld(page)["remote"] == "remote"

    def test_a_posting_without_a_title_or_company_is_not_a_job(self):
        """`normalise.from_scrape` would reject it anyway; refusing here keeps the ladder honest
        about which rung actually answered."""
        page = JSON_LD.replace('"title":"Graduate Software Engineer",', "")
        assert extract._from_json_ld(page) is None

    @pytest.mark.parametrize(
        "page",
        [
            "<html><body>no markup at all</body></html>",
            '<script type="application/ld+json">{not json</script>',
            '<script type="application/ld+json"></script>',
            '<script type="application/ld+json">{"@type":"Organization","name":"Fernhill"}</script>',
        ],
    )
    def test_a_page_with_nothing_usable_falls_through_quietly(self, page):
        assert extract._from_json_ld(page) is None

    def test_a_loosely_typed_field_does_not_raise(self):
        """JSON-LD in the wild puts numbers, lists and objects where strings are documented."""
        page = """<script type="application/ld+json">
        {"@type":"JobPosting","title":["Graduate","Engineer"],
         "hiringOrganization":{"name":"Fernhill AI"},"description":12345,
         "jobLocation":{"address":{"addressLocality":"London"}}}
        </script>"""
        card = extract._from_json_ld(page)
        assert card["title"] == "Graduate Engineer"
        assert card["description"] == "12345"


class TestTheLadderOrder:
    """Cheapest rung first, and a rung is only reached when the one above it couldn't answer."""

    def test_json_ld_is_preferred_over_the_model(self, monkeypatch):
        monkeypatch.setattr(extract, "_get", lambda _url: JSON_LD)
        asked: list[str] = []

        def parse(text):
            asked.append(text)
            return {"title": "x", "company": "y"}

        card, how = extract.card("https://board.example/jobs/1", parse=parse)
        assert how == "json-ld"
        assert card["company"] == "Fernhill AI"
        assert asked == [], "the model was asked about a page the parser had already read"

    def test_the_browser_is_tried_when_plain_http_has_no_markup(self, monkeypatch):
        monkeypatch.setattr(extract, "_get", lambda _url: "<html><body>a React shell</body></html>")
        card, how = extract.card("https://board.example/jobs/1", fetch_html=lambda _url: JSON_LD)
        assert how == "json-ld-rendered"
        assert card["title"] == "Graduate Software Engineer"

    def test_the_model_is_the_last_free_rung(self, monkeypatch):
        page = "<html><body>" + ("A real job description with plenty of words. " * 20) + "</body></html>"
        monkeypatch.setattr(extract, "_get", lambda _url: page)
        card, how = extract.card(
            "https://board.example/jobs/1",
            parse=lambda _text: {"title": "Graduate Engineer", "company": "Harbour", "location": {"raw": "Bristol, UK"}},
        )
        assert how == "model"
        assert (card["title"], card["company"], card["location"]) == ("Graduate Engineer", "Harbour", "Bristol, UK")

    def test_the_model_keeps_the_pages_own_words_as_the_description(self, monkeypatch):
        """LTR-02 quotes the description verbatim, so a model's paraphrase of it would make a
        "word for word" company fact a lie."""
        page = "<html><body>" + ("Distinctive phrasing only this page has. " * 20) + "</body></html>"
        monkeypatch.setattr(extract, "_get", lambda _url: page)
        card, _ = extract.card(
            "https://board.example/jobs/1",
            parse=lambda _text: {"title": "T", "company": "C", "description": "a summary the model wrote"},
        )
        assert "Distinctive phrasing only this page has." in card["description"]
        assert "a summary the model wrote" not in card["description"]

    def test_an_unrenderable_page_is_reported_rather_than_guessed_at(self, monkeypatch):
        monkeypatch.setattr(extract, "_get", lambda _url: None)
        card, how = extract.card("https://board.example/jobs/1")
        assert card is None and how == "unreachable"

    def test_a_page_too_empty_to_read_never_reaches_the_model(self, monkeypatch):
        """Below `MIN_DESCRIPTION` there is nothing but navigation, and a model would be
        guessing from a cookie banner."""
        monkeypatch.setattr(extract, "_get", lambda _url: "<html><body>Accept cookies</body></html>")
        asked: list[str] = []
        card, how = extract.card("https://board.example/jobs/1", parse=lambda t: asked.append(t) or None)
        assert card is None and how == "unreadable"
        assert asked == []


class TestAPageThatIsntAJob:
    """Seen in Logfire on a live harvest: an advert slot ("featured graduate employer") reached the
    model, which rightly found no title — that failed the schema twice and then fell through to a
    paid Firecrawl scrape of the same non-job. "Not a job" is an answer now, and it ends the ladder."""

    PAGE = "<html><body>" + ("Featured employer. Advertise with us. " * 20) + "</body></html>"

    def test_the_model_can_say_so_without_failing_the_schema(self):
        from wera_agent.schemas.llm_outputs import NormalisedJob

        base = {"location": {"raw": "London"}, "remote": None, "language": "en"}
        assert not NormalisedJob.model_validate({**base, "isJobPosting": False, "title": None, "company": None}).is_job_posting
        with pytest.raises(Exception, match="isJobPosting"):
            NormalisedJob.model_validate({**base, "isJobPosting": True, "title": None, "company": "Acme"})

    def test_the_ladder_stops_with_its_own_verdict(self, monkeypatch):
        monkeypatch.setattr(extract, "_get", lambda _url: self.PAGE)
        card, how = extract.card("https://board.example/job/ad/1", parse=lambda _t: {extract.NOT_A_JOB: True})
        assert card is None and how == "not-a-job"

    def test_nothing_is_paid_for_it_and_it_is_remembered(self, monkeypatch):
        from wera_agent.sources import firecrawl_search

        monkeypatch.setattr(extract, "_get", lambda _url: self.PAGE)
        monkeypatch.setattr(firecrawl_search, "_client", lambda: pytest.fail("Firecrawl was asked about a page already read"))
        remembered: list = []

        class Cache:
            def card(self, *_a):
                return False, None  # nothing cached yet

            def put_card(self, url, _schema, card):
                remembered.append((url, card))

        outcome = firecrawl_search.SearchOutcome()
        card = firecrawl_search.scrape_card(
            "https://board.example/job/ad/1",
            firecrawl_search.CARD_SCHEMA,
            outcome,
            firecrawl_search.SearchCaps(),
            cache=Cache(),
            parse=lambda _t: {extract.NOT_A_JOB: True},
        )
        assert card is None
        assert (outcome.credits_used, outcome.fallback_pages) == (0, 0)
        assert remembered == [("https://board.example/job/ad/1", None)]


class TestAPageOfJobs:
    """SRCH-21. A results page with several `JobPosting`s came back as its first job, saved with
    the results page as its link. Several postings now means a listing, and the ladder stops."""

    LISTING = """<html><head>
<script type="application/ld+json">[
 {"@type":"JobPosting","title":"Graduate Software Engineer","hiringOrganization":{"name":"Accenture"}},
 {"@type":"JobPosting","title":"Systems Engineer","hiringOrganization":{"name":"BAE Systems"}}
]</script></head><body></body></html>"""

    def test_several_postings_is_a_listing(self, monkeypatch):
        monkeypatch.setattr(extract, "_get", lambda _url: self.LISTING)
        model = []
        card, how = extract.card("https://www.gradcracker.com/hub/1/x/graduate-job/12345", parse=model.append)
        assert (card, how) == (None, "listing")
        assert model == []  # nothing asked the model to pick one

    def test_the_same_posting_twice_is_still_one_job(self, monkeypatch):
        twice = JSON_LD.replace("</script>", "</script>" + JSON_LD.split("<head>")[1].split("</head>")[0])
        monkeypatch.setattr(extract, "_get", lambda _url: twice)
        card, how = extract.card("https://board.example/job/x/1234567")
        assert how == "json-ld" and card["company"] == "Fernhill AI"

    def test_a_listing_found_after_rendering_too(self, monkeypatch):
        monkeypatch.setattr(extract, "_get", lambda _url: "<html><body>a React shell</body></html>")
        card, how = extract.card("https://board.example/job/x/1234567", fetch_html=lambda _url: self.LISTING)
        assert (card, how) == (None, "listing")


class TestAClosedPage:
    def test_a_not_found_title(self, monkeypatch):
        page = (
            "<html><head><title>Job Posting Not Found</title></head><body>" + "Browse our other roles. " * 30 + "</body></html>"
        )
        monkeypatch.setattr(extract, "_get", lambda _url: page)
        assert extract.card("https://board.example/job/x/1234567", parse=lambda _t: pytest.fail("model asked")) == (
            None,
            "closed",
        )

    def test_a_closure_banner(self, monkeypatch):
        page = "<html><body><p>This vacancy has expired.</p>" + "<p>Role details follow.</p>" * 30 + "</body></html>"
        monkeypatch.setattr(extract, "_get", lambda _url: page)
        assert extract.card("https://board.example/job/x/1234567") == (None, "closed")

    def test_neither_is_paid_for(self, monkeypatch):
        from wera_agent.sources import firecrawl_search

        page = "<html><head><title>Not Found</title></head><body></body></html>"
        monkeypatch.setattr(extract, "_get", lambda _url: page)
        monkeypatch.setattr(firecrawl_search, "_client", lambda: pytest.fail("Firecrawl was asked about a closed page"))
        outcome = firecrawl_search.SearchOutcome()
        card = firecrawl_search.scrape_card(
            "https://board.example/job/x/1234567", firecrawl_search.CARD_SCHEMA, outcome, firecrawl_search.SearchCaps()
        )
        assert card is None and outcome.credits_used == 0


class TestAPostingThatRedirectsAway:
    """SRCH-23: a board that took a posting down often keeps the address and redirects to its
    search page. That redirect is the closure, and nothing is paid to read the search page."""

    @respx.mock
    def test_a_redirect_to_a_results_page_is_closed(self, outbound):
        respx.get("https://builtinlondon.uk/job/backend-engineer/12000001").mock(
            return_value=httpx.Response(302, headers={"location": "https://builtinlondon.uk/jobs?search=backend"})
        )
        respx.get("https://builtinlondon.uk/jobs?search=backend").mock(return_value=httpx.Response(200, text="<html>jobs</html>"))
        assert extract.card("https://builtinlondon.uk/job/backend-engineer/12000001") == (None, "closed")

    @respx.mock
    def test_a_posting_that_moved_to_another_posting_address_is_still_read(self, outbound):
        respx.get("https://builtinlondon.uk/job/backend-engineer/12000001").mock(
            return_value=httpx.Response(301, headers={"location": "https://builtinlondon.uk/job/backend-engineer-ii/12000001"})
        )
        respx.get("https://builtinlondon.uk/job/backend-engineer-ii/12000001").mock(
            return_value=httpx.Response(200, text=JSON_LD)
        )
        card, how = extract.card("https://builtinlondon.uk/job/backend-engineer/12000001")
        assert how == "json-ld" and card["title"] == "Graduate Software Engineer"
