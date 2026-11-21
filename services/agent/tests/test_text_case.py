"""`text.display_case` (20 Nov): names and companies written all in capitals, in ordinary case."""

import pytest

from wera_agent.domain.text import display_case


@pytest.mark.parametrize(
    ("raw", "brand", "shown"),
    [
        ("ELEANOR VANCE", False, "Eleanor Vance"),
        ("SEAN O'BRIEN", False, "Sean O'Brien"),
        ("NATIONAL BANK OF CANADA", True, "National Bank of Canada"),
        ("HSBC UK", True, "HSBC UK"),
        ("AECOM", True, "AECOM"),
        ("CITI", True, "CITI"),
        ("McLean Capital", True, "McLean Capital"),
        ("JPMorgan Chase", True, "JPMorgan Chase"),
        ("deliveroo", True, "deliveroo"),
    ],
)
def test_display_case(raw, brand, shown):
    assert display_case(raw, brand=brand) == shown
