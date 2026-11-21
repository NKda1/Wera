"""Citation guard (SPEC §8.5): every model-written line must be traceable to the master-CV
entries it cites. Runs before anything reaches the review screen."""

import re

from ..schemas.cv import MasterCV
from ..schemas.pack import Cited, GuardResult, Violation
from .vocab import backing_text, cv_line_index, master_cv_text, normalise_text, text_has_term

# Terms the guard watches for: master skills, aliases, job keywords and a common tech list.
COMMON_TECH = ["Docker", "Kubernetes", "AWS", "GCP", "Azure", "Rust", "Java", "React", "Django", "Kafka", "Terraform"]

NUMBER = re.compile(r"\d+(?:[.,]\d+)?\s?%?")


def check_cited(
    items: list[tuple[str, Cited]],
    cv: MasterCV,
    job_keywords: list[str] | None = None,
    *,
    unsourced_ok: frozenset[str] | set[str] = frozenset(),
    names: bool = False,
) -> GuardResult:
    """`unsourced_ok` names lines allowed to cite nothing — a letter's closing courtesy, which
    asks for a conversation and claims nothing. They are still checked: any number in one fails,
    and any skill it names has to be in the CV's own facts, because there are no lines to carry it.

    `names` adds `institution_violations`, for prose a model wrote (the letter). A CV line is a
    copy of the master and names its school however the master does."""
    lines = cv_line_index(cv)
    backing = backing_text(cv)
    vocabulary = [*[t for s in cv.skills for t in [s.name, *(s.aliases or [])]], *(job_keywords or []), *COMMON_TECH]
    violations: list[Violation] = []

    for location, item in items:
        if item.edited_by_user:
            continue  # G2-02: the user is the authority on their own edits
        if not item.source_ids and location not in unsourced_ok:
            violations.append(Violation(location=location, rule="sources", detail="No master-CV source cited"))
            continue
        missing = [i for i in item.source_ids if i not in lines]
        if missing:
            violations.append(Violation(location=location, rule="sources", detail=f"Unknown source id: {', '.join(missing)}"))
            continue
        source_text = "\n".join(lines[i]["text"] for i in item.source_ids)

        for n in NUMBER.findall(item.text):
            if n.strip() not in source_text:
                violations.append(Violation(location=location, rule="numbers", detail=f'"{n.strip()}" is not in the cited lines'))
        for term in vocabulary:
            if text_has_term(item.text, term) and not text_has_term(source_text, term) and not text_has_term(backing, term):
                violations.append(
                    Violation(location=location, rule="terms", detail=f'"{term}" is not in the cited lines or your CV')
                )
        if names:
            violations += institution_violations(location, item.text, cv)
    return GuardResult(pass_=not violations, violations=violations, repaired=False)


# Words that make a run of capitalised words the name of a place of study.
_SCHOOL_WORDS = frozenset(
    [
        "university",
        "universität",
        "universidad",
        "université",
        "università",
        "college",
        "school",
        "institute",
        "institut",
        "academy",
        "polytechnic",
        "conservatoire",
        "conservatory",
        "faculty",
    ]
)
# Words that join a name's capitalised words without being distinctive themselves.
_JOINERS = frozenset(["of", "and", "&", "for", "the", "in", "de", "du", "la", "le", "der", "des"])
# A run of capitalised words (or acronyms), allowing the joiners between them: "the London School
# of Economics", "LSE School of Economics", "King's College London".
# No full stops inside a word, so a run never crosses into the next sentence.
_CAPITALISED_RUN = re.compile(r"(?:[A-Z][\w'’&-]*)(?:\s+(?:(?:of|and|&|for|the|in|de|du|la|le|der|des)\s+)*[A-Z][\w'’&-]*)*")
_ACRONYM = re.compile(r"\(([A-Z][A-Z&.]{1,7})\)\s*$")


def _words(text: str) -> list[str]:
    return [w.strip("'’.,()").casefold() for w in text.split() if w.strip("'’.,()")]


def _said(run: str) -> str:
    """A capitalised run as a name: a leading "The" is the sentence's, not the name's, and so is
    a full stop at the end."""
    return re.sub(r"^the\s+", "", run, flags=re.I).strip().rstrip(".,;:'’")


def institution_violations(location: str, text: str, cv: MasterCV) -> list[Violation]:
    """A place of study the letter names otherwise than the CV does (20 Nov).

    A live letter wrote "the LSE School of Economics" for "London School of Economics and
    Political Science (LSE)": a name nobody uses, in the one line of a graduate's letter a reader
    is sure to check. `check_cited` couldn't see it — every word of it is in the CV.

    A run of capitalised words names one of the CV's institutions when it has a word like
    "School" or "University", or the CV's own abbreviation, and shares a distinctive word or the
    abbreviation with that institution. It passes if it is written somewhere in the CV exactly so
    — the entry itself, the name without its "(LSE)", or a form the summary or a bullet uses —
    and the abbreviation alone passes because the CV gives it. Anything else is a violation, so
    the paragraph is written again.
    """
    # Whole names only: "London School" is inside the CV's "London School of Economics", and is
    # still not a name the CV uses.
    written = {
        _said(run).casefold()
        for line in master_cv_text(cv).splitlines()
        for run in _CAPITALISED_RUN.findall(normalise_text(line))
    }
    out: list[Violation] = []
    for entry in cv.education:
        name = normalise_text(entry.institution)
        found = _ACRONYM.search(name)
        acronym = found.group(1).rstrip(".") if found else None
        base = _ACRONYM.sub("", name).strip()
        distinctive = {w for w in _words(base) if w not in _JOINERS and w not in _SCHOOL_WORDS}
        rest = normalise_text(text)
        # The entry as written, and without its "(LSE)", come out first; the abbreviation alone
        # stays in, so "LSE School of Economics" is still read as one run.
        for form in (name, base):
            rest = re.sub(re.escape(form), " ", rest, flags=re.I)
        for run in _CAPITALISED_RUN.findall(rest):
            said = _said(run)
            words = _words(said)
            # A school word, not the abbreviation alone: "the LSE Investment Society" is a club.
            schoolish = bool(set(words) & _SCHOOL_WORDS)
            related = bool(set(words) & distinctive) or (acronym is not None and acronym.casefold() in words)
            if not (schoolish and related) or said.casefold() in written:
                continue
            out.append(
                Violation(
                    location=location,
                    rule="names",
                    detail=f'"{said}" isn\'t how your CV names {entry.institution}; write it as the CV does',
                )
            )
    return out
