"""SRCH-11: which country a place is in, and what a profile's "where" means (16 Nov).

A live search of Switzerland and "London, UK" found no Swiss jobs: a posting naming only its city
("Zürich") matched no country, so it was hidden, and the country was searched by its name.
"""

import pytest

from wera_agent.domain import places


class TestCountryOf:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Zürich", {"CH"}),
            ("Zurich", {"CH"}),
            ("Zürich, CH", {"CH"}),
            ("london, england, GB", {"GB"}),
            ("London, England, GBR", {"GB"}),
            ("Dubai - United Arab Emirates", {"AE"}),
            ("Belfast", {"GB"}),
            ("Kfar Saba, 02, IL", {"IL"}),
            ("Chicago, en-us", {"US"}),
            ("Toronto, ON", {"CA"}),
            ("Milton Keynes area,England UK", {"GB"}),
            ("Oxford Business Park North", {"GB"}),
            ("Remote in London", {"GB"}),
            ("Anywhere in the UK", {"GB"}),
            ("Geneva", {"CH"}),  # not Geneva, Illinois
            ("Paris", {"FR"}),  # not Paris, Texas
        ],
    )
    def test_a_place_is_read_to_its_country(self, raw, expected):
        assert places.country_of(raw) == expected

    def test_a_us_state_and_an_iso_code_are_told_apart_by_the_city(self):
        """CA is California and Canada; DE is Delaware and Germany."""
        assert places.country_of("San Francisco, CA") == {"US"}
        assert places.country_of("Newark, DE") == {"US"}
        assert places.country_of("Munich, DE") == {"DE"}

    def test_a_name_two_countries_share_is_both(self):
        assert places.country_of("Birmingham") == {"GB", "US"}
        assert places.country_of("Birmingham, UK") == {"GB"}

    @pytest.mark.parametrize("raw", ["Hybrid", "Contact us", "Multiple Locations", "Head Office", "Melton"])
    def test_what_isnt_a_known_place_is_no_country(self, raw):
        """ "us" and "in" are words before they are codes; Melton is too small to be listed."""
        assert places.country_of(raw) == set()


class TestCountryCode:
    """19 Nov: `location.country` was each source's own words ("United Kingdom - Deliveroo", "EU)")
    or nothing at all, so the jobs couldn't be counted by country."""

    @pytest.mark.parametrize(
        ("raw", "code"),
        [
            ("United Kingdom - Deliveroo", "GB"),
            ("EU)", None),
            ("San Francisco, CA", "US"),
            ("Manchester", "GB"),  # Manchester, NH is under a third its size
            ("Cambridge", None),  # England's and Massachusetts' are near enough the same
            ("Remote", None),
            ("Hybrid", None),
            ("London, England", "GB"),
            ("Zürich", "CH"),
            ("Remote-Friendly (Travel Required) | San Francisco, CA", "US"),
            ("London, UK; New York, US", "GB"),
        ],
    )
    def test_a_posting_has_one_country_or_none(self, raw, code):
        assert places.country_code(raw) == code

    def test_the_country_a_source_gave_is_read_when_the_place_says_nothing(self):
        assert places.country_code("Hybrid", "United Kingdom") == "GB"
        assert places.country_code("Not stated", "US") == "US"
        assert places.country_code("Hybrid", "EU)") is None

    def test_every_job_saved_gets_the_code(self):
        from wera_agent.sources.normalise import from_scrape

        job = from_scrape(
            {"title": "Data Engineer", "company": "Acme", "locationRaw": "Zürich"},
            url="https://acme.example/1",
            site="acme.example",
            markdown="x" * 300,
            now=0,
        )
        assert (job.location.country, job.location.raw) == ("CH", "Zürich")


class TestCityName:
    """19 Nov: haystackapp.io writes "london, england, GB", and the Jobs screen's Where filter
    offered London and london as two places (and Manchester and manchester)."""

    @pytest.mark.parametrize(
        ("city", "country", "spelt"),
        [
            ("london", "GB", "London"),
            ("LONDON", "GB", "London"),
            ("manchester", "GB", "Manchester"),
            ("zurich", "CH", "Zürich"),  # the gazetteer's own spelling
            ("ZÜRICH", None, "Zürich"),
            ("Krakow", "PL", "Kraków"),
            ("  new   york ", "US", "New York"),
            ("birmingham", "US", "Birmingham"),
        ],
    )
    def test_a_place_the_gazetteer_knows_takes_its_spelling(self, city, country, spelt):
        assert places.city_name(city, country) == spelt

    def test_a_place_is_not_renamed_for_another_name_it_has(self):
        """Munich is what the posting said; München is the gazetteer's name for the same city."""
        assert places.city_name("Munich") == "Munich"

    @pytest.mark.parametrize(
        ("city", "spelt"),
        [("MESTRE", "Mestre"), ("tiny-town on sea", "Tiny-Town On Sea"), ("o'fallon bay", "O'Fallon Bay")],
    )
    def test_an_unknown_place_in_one_case_is_title_cased(self, city, spelt):
        assert places.city_name(city) == spelt

    @pytest.mark.parametrize("city", ["McLean", "São Paulo", "Mc Gill Heights", "NYC", "DIFC", "EC2A 4NE", "area 51"])
    def test_anything_else_is_left_as_written(self, city):
        """Mixed case is how its people write it; an acronym or a postcode is not a word."""
        assert places.city_name(city) == city

    def test_no_city_is_no_city(self):
        assert places.city_name(None) is None
        assert places.city_name("   ") is None

    def test_every_job_saved_gets_one_spelling_and_the_same_key(self):
        from wera_agent.sources.normalise import from_scrape

        def read(where: str):
            return from_scrape(
                {"title": "Software Engineer", "company": "Skeleton Crew", "locationRaw": where},
                url="https://haystackapp.io/jobs/1",
                site="haystackapp.io",
                markdown="x" * 300,
                now=0,
            )

        lower, proper = read("london, england, GB"), read("London, England, GB")
        assert (lower.location.city, lower.location.country) == ("London", "GB")
        assert lower.location.raw == "london, england, GB"  # the words are left as the page wrote them
        assert lower.dedupe_key == proper.dedupe_key

    def test_a_job_with_no_url_keeps_the_key_it_had_when_its_city_gains_an_accent(self):
        """The key comes from the city as the source wrote it, so respelling never moves it."""
        from wera_agent.domain import dedupe
        from wera_agent.sources.normalise import from_card

        job = from_card(title="Analyst", company="Acme", location_raw="zurich", url=None, detail="x", now=0)
        assert job.location.city == "Zürich"
        assert job.dedupe_key == dedupe.dedupe_key(ats=None, apply_url=None, company="Acme", title="Analyst", city="zurich")


class TestTheCountries:
    def test_every_country_can_be_picked(self):
        """16 Nov: someone looking in Dubai had no way to say so."""
        assert len(places.COUNTRIES) > 200
        assert places.COUNTRIES["AE"].region == "middle_east"
        assert "uae" in places.COUNTRIES["AE"].aliases
        assert places.COUNTRIES["GB"].name == "United Kingdom" and places.COUNTRIES["MX"].region == "latin_america"

    def test_a_country_is_searched_where_its_jobs_are(self):
        assert places.hubs("CH") == ["Zürich", "Geneva", "Basel"]
        assert places.hubs("JP")[0] == "Tokyo"


class TestWhere:
    def test_a_city_goes_to_its_own_country(self):
        wanted = places.where(["CH", "GB"], ["London, UK"])
        assert wanted.countries == ["CH", "GB"] and wanted.cities == {"GB": ["London"]}

    def test_a_city_outside_the_ticked_countries_adds_its_own(self):
        assert places.where(["GB"], ["Dubai"]).countries == ["GB", "AE"]

    def test_a_shared_name_goes_to_the_ticked_country_else_the_largest_place(self):
        assert places.where(["US"], ["Birmingham"]).cities == {"US": ["Birmingham"]}
        assert places.where([], ["Birmingham"]).cities == {"GB": ["Birmingham"]}

    def test_remote_is_not_a_city(self):
        assert places.where([], ["Remote"]) == places.Where([], {}, [])
