"""UPSK-07 (21 Nov): which kind of role a job is, from its title.

A finance CV's Upskills listed C++ among its gaps, asked by quantitative roles — which sit between
finance and computer science. Each job now carries a role family, so a gap says who asks for it.
"""

import pytest

from wera_agent.domain.role_family import family_of, own_family


@pytest.mark.parametrize(
    ("title", "family"),
    [
        ("Quantitative Research Analyst - PIMCO", "quantitative"),
        ("Quant Developer", "quantitative"),
        ("Financial Engineer", "quantitative"),
        ("Etrading Product Analyst", "quantitative"),
        ("Investment Banking Analyst, M&A", "finance"),
        ("Equity Research Analyst", "finance"),
        ("Risk Analyst, Private Markets", "finance"),
        ("Financieel analist", "finance"),
        ("Data Scientist", "data"),
        ("AI Research Scientist", "data"),
        ("Graduate Software Engineer", "software"),
        ("Technology - Application Development, Summer Analyst", "software"),
        ("Trainee Solicitor", "legal"),
        ("Graduate Commercial Analyst", "consulting"),
        ("Listing Analyst intern", "other"),
    ],
)
def test_each_title_finds_its_family(title, family):
    assert family_of(title) == family


def test_the_persons_family_is_where_their_titles_fall():
    finance = ["Investment Banking Analyst", "Corporate Finance Analyst", "Equity Research Analyst", "Junior Financial Modeller"]
    assert own_family(finance) == "finance"
    assert own_family(["Graduate Software Engineer", "Backend Developer"]) == "software"
    assert own_family(["Data Scientist", "Backend Developer"]) is None  # an even split names no field
