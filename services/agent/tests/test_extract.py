"""SRCH-05: reading a job off a page without paying Firecrawl for it.

The ladder in `sources.extract` replaced a 5-credit JSON scrape per page, which was 5 of every 7
credits a run spent. These tests cover the rungs that cost nothing, because those are the ones
that have to be right — a rung that silently stops answering doesn't fail, it just gets expensive.
"""

import pytest

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
