"""Which kind of role a job is, from its title (UPSK-07, 21 Nov).

A finance CV's Upskills listed C++ among its gaps: the asks came from quantitative roles — quant
analysts, quant developers, systematic traders — which sit between finance and computer science
and ask for both. A skill a quant desk wants is a real direction for a finance graduate, but a
different one from what an investment bank's analyst programme wants, and Upskills mixed the two.

So every job, scored or nearly matched, is given a role family from its title, and the person's own
family is the one their target titles fall in. Upskills says which families ask for each gap,
flags a gap that comes mostly from outside the person's own family, and can be filtered to one.

Families are ordered: the first that matches wins. Quantitative comes before finance and software
because a "Quantitative Developer" or a "Financial Engineer" is a quant role, not a developer or a
banker; data comes before software because a "Data Engineer" is the data track. Each family is
named the way a person would scan for it, and covers every career WERA serves, not only finance.
"""

import re
from collections import Counter

from .vocab import normalise_term

# (id, label, title pattern). Matched on the lower-cased, normalised title.
FAMILIES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "quantitative",
        "Quantitative roles",
        re.compile(
            r"\b(quant\w*|systematic|algorithmic|algo|strats?|financial engineer\w*|stochastic|derivatives? pricing|"
            r"(?:junior |graduate )?trader|trading (?:analyst|assistant|desk|development|product)|e-?trading|market making|"
            r"risk model\w*|model validation)\b"
        ),
    ),
    (
        "data",
        "Data & analytics roles",
        re.compile(
            r"\b(data (?:scien\w*|analy\w*|engineer\w*)|machine learning|ml engineer|ai engineer|analytics engineer|"
            r"business intelligence|bi (?:analyst|developer)|insights? analyst|statistician|research (?:scientist|engineer))\b"
        ),
    ),
    (
        "software",
        "Software & engineering roles",
        re.compile(
            r"\b(software|developer|programmer|devops|sre|site reliability|backend|back-end|frontend|front-end|"
            r"full[- ]stack|platform engineer|cloud engineer|systems engineer|security engineer|qa engineer|"
            r"test engineer|mobile engineer|engineer(?:ing)? intern|application development|technology analyst)\b"
        ),
    ),
    (
        "finance",
        "Finance & banking roles",
        re.compile(
            r"\b(invest\w*|bank\w*|m&a|mergers|corporate finance|equity|equities|valuation|financial analys\w*|"
            r"finance analyst|fp&a|asset management|wealth|private equity|venture|credit|fixed income|portfolio|"
            r"fund\w*|treasury|audit\w*|accountan\w*|accounting|tax|actuar\w*|insurance|capital markets|research analyst|"
            r"financial (?:planning|controller)|controller|financ\w*|risk analyst|restructuring|high yield|ibcm|"
            # 21 Nov, from live titles: research, macro and markets desks, and financial crime.
            r"research|macro|economic\w*|asset allocation|commodit\w*|fraud|account analyst)\b"
        ),
    ),
    (
        "consulting",
        "Consulting, strategy & product roles",
        re.compile(
            r"\b(consult\w*|strategy|strategic|business analyst|transformation|advisory|product (?:management|manager|analyst)|"
            r"commercial analyst)\b"
        ),
    ),
    ("legal", "Legal roles", re.compile(r"\b(law|legal|solicitor|barrister|paralegal|lawyer|counsel|compliance)\b")),
    (
        "marketing",
        "Marketing & communications roles",
        re.compile(r"\b(marketing|brand|content|communications|pr|public relations|social media|seo|copywriter|growth)\b"),
    ),
    (
        "sales",
        "Sales & business development roles",
        re.compile(r"\b(sales|account executive|account manager|business development|bdr|sdr|customer success)\b"),
    ),
    (
        "operations",
        "Operations & people roles",
        re.compile(
            r"\b(operations|operational|procurement|supply chain|logistics|hr|human resources|recruit\w*|people|office|"
            r"implementation)\b"
        ),
    ),
    (
        "engineering",
        "Engineering (non-software) roles",
        re.compile(r"\b(mechanical|electrical|civil|chemical|aerospace|structural|manufacturing|process engineer)\b"),
    ),
    (
        "health",
        "Health & life sciences roles",
        re.compile(r"\b(clinical|nurse|nursing|pharma\w*|medical|health\w*|biolog\w*|laboratory|scientist)\b"),
    ),
]

OTHER = "other"
LABELS: dict[str, str] = {fid: label for fid, label, _ in FAMILIES} | {OTHER: "Other roles"}


def family_of(title: str) -> str:
    """The role family a job title belongs to; `OTHER` when none matches."""
    text = normalise_term(title or "")
    return next((fid for fid, _label, pattern in FAMILIES if pattern.search(text)), OTHER)


def own_family(titles: list[str]) -> str | None:
    """The family the person's target titles mostly fall in: their own field. None when the titles
    name no family, or split evenly between two."""
    counts = Counter(f for f in (family_of(t) for t in titles) if f != OTHER)
    if not counts:
        return None
    ranked = counts.most_common(2)
    if len(ranked) == 2 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]
