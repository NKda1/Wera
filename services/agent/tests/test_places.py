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
