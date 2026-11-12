"""ATS check (SPEC §8.3, ATSC-01…05): coverage and format checks, computed in code."""

import math
import re
import unicodedata

from ..domain.scoring import ats_coverage, js_round
from ..domain.vocab import education_text, master_cv_text, role_header, skill_text, text_has_term
from ..schemas.cv import MasterCV
from ..schemas.jobs import FitEvidence, Job
from ..schemas.pack import AtsKeyword, AtsReport, CoverLetter, FormatCheck, TailoredCV

# Roughly two lines of skills per page, at `estimate_pages`' 95 characters a line. A character
# budget rather than a count, because ten long skill names and ten short ones are not the same
# amount of page.
SKILLS_LINE_CHARS = 190


def tailored_text(doc: TailoredCV, cv: MasterCV) -> str:
    return "\n".join([*_body_lines(doc, cv), " · ".join(doc.skills)])


def _body_lines(doc: TailoredCV, cv: MasterCV) -> list[str]:
    """The document without its skills line — what the skills line is reconciled *against*.

    **Everything the export prints**, which is why the master CV is needed here: `export/content.ts`
    renders role headers and the whole education section from the master CV (TAIL-02), so they are
    on the page a parser reads even though the pack doesn't carry their text.

    Leaving them out understated coverage in a way that then misled the applicant twice over. A
    post asking for "Computer Science" was marked unmatched against a CV reading "BA (Hons) in
    Computer Science (Tripos) — University of Cambridge", so the term was reported as a gap to go
    and fill, and the headline percentage was measuring a document narrower than the PDF.
    """
    roles = {r.id: r for r in [*cv.experience, *cv.projects]}
    headers = [role_header(roles[i.role_id]) for s in doc.sections for i in s.items if i.role_id in roles]
    bullets = [b.text for s in doc.sections for i in s.items for b in i.bullets]
    return [doc.summary.text, *headers, *bullets, *[education_text(e) for e in cv.education]]


def estimate_pages(doc: TailoredCV, cv: MasterCV) -> float:
    def lines(chars: int, per_line: int) -> int:
        return max(1, math.ceil(chars / per_line))

    total = 4.0  # name, contact line, rule, spacing
    total += lines(len(doc.summary.text), 95) + 0.5
    for s in doc.sections:
        total += 1.6  # heading
        for item in s.items:
            total += 1.4  # role header
            for b in item.bullets:
                total += lines(len(b.text), 80) + 0.25
    total += 1.6 + len(cv.education) * 1.4  # education
    total += 1.6 + lines(len(" · ".join(doc.skills)), 95)  # skills
    return js_round((total / 46) * 10) / 10


# ATSC-05, the same rule `export/lint.ts` applies to the file it renders: letters, digits and
# single hyphens. A name with an accent or an apostrophe fails it, which is the point of checking.
FILE_NAME = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-(?:CV|Letter)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\.(?:pdf|docx)")


def _name_part(s: str) -> str:
    """`export/content.ts`'s `part`, so the name checked here is the name the file gets."""
    kept = "".join(c for c in unicodedata.normalize("NFKD", s) if c.isalnum() or c == " ")
    return "-".join(kept.split())


def cv_file_name(cv: MasterCV, company: str) -> str:
    """ATSC-05: First-Last-CV-Company.pdf, built the way the export builds it.

    This used to be its own spelling of the rule, and the two disagreed: "Siobhán O'Neill" became
    `Siobhán-O'Neill-CV-…` here and `Siobhan-ONeill-CV-…` on the actual file, so a check on this
    name was a check on a file nobody would download.
    """
    return f"{_name_part(cv.person.name) or 'CV'}-CV-{_name_part(company) or 'Company'}.pdf"


def _skill_for_term(cv: MasterCV, term: str) -> str | None:
    """The CV's own spelling of a keyword, if the CV lists it as a skill.

    Only a *named* skill counts. A keyword that appears only inside a bullet is backed by the CV
    but is not a skill the applicant claims, and promoting it to the skills line would be WERA
    writing a claim rather than selecting one. ATSC-04 calls that a gap, which is the right
    answer: "add only if true" is the applicant's call to make.
    """
    return next((s.name for s in cv.skills if text_has_term(skill_text(s), term)), None)


def _fit_line(names: list[str], cv_pages: int) -> list[str]:
    """As many skills as the line has room for, in the order given.

    Order is therefore load-bearing: whatever `reconcile_skills` puts first is what survives.
    """
    budget = cv_pages * SKILLS_LINE_CHARS
    out: list[str] = []
    length = 0
    for name in names:
        extra = len(name) + (3 if out else 0)  # the " · " separator
        if out and length + extra > budget:
            break
        out.append(name)
        length += extra
    return out


def reconcile_skills(doc: TailoredCV, *, cv: MasterCV, evidence: FitEvidence, cv_pages: int, threshold: float) -> TailoredCV:
    """ATSC-02/03 without a model: cover what the post asks for by selecting from the CV's own
    skills, instead of asking the writing tier to tailor the whole CV again.

    This replaces the re-tailor loop. Mean coverage measured **48%** against a 0.75 threshold, so
    `should_retry_tailoring` fired on nearly every pack and re-ran `cv.tailor` *and*
    `letter.write` up to three times — on a tier that returned usable output 63.6% of the time.
    That loop was most of a pack's wall clock and most of its failures, and it was paying a model
    to solve a problem that needs no model: a keyword the CV already backs just has to appear.

    Stops at the threshold rather than covering everything it could. A skills line carrying every
    term the post happened to mention clears the robot and reads as keyword spam to the human who
    sees it next, and the human is the one who decides.
    """
    body_lines = _body_lines(doc, cv)
    body = "\n".join(body_lines)
    # What the post asks for that the CV can back, best-value first: a term the body doesn't
    # already carry is the one that moves coverage, and `required` counts double in
    # `ats_coverage`. `sorted` is stable, so the post's own order breaks ties.
    ranked = sorted(evidence.keywords, key=lambda k: (text_has_term(body, k.term), k.weight != "required"))

    chosen: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name.casefold() not in seen:
            seen.add(name.casefold())
            chosen.append(name)

    for keyword in ranked:
        # Measured on the line that will actually ship, not on everything chosen so far. The
        # skills line is capped by `_fit_line` *after* this loop, so a run that picked more terms
        # than fit on the page used to stop at a threshold the stored document then missed — the
        # function reported success against a line it had not produced. Checking the budgeted
        # line means the loop either clears the threshold for real or runs out of keywords
        # trying, and `_fit_line` keeps the best-ranked of them either way.
        if _coverage(body_lines, _fit_line(chosen, cv_pages), evidence) >= threshold:
            break
        name = _skill_for_term(cv, keyword.term)
        if name is not None:
            add(name)

    # Then the model's own picks, for whatever space is left. It chose them against this job, so
    # they are better filler than master-CV order would be.
    for name in doc.skills:
        add(name)

    doc.skills = _fit_line(chosen, cv_pages)
    return doc


def _coverage(body_lines: list[str], skills: list[str], evidence: FitEvidence) -> float:
    """What coverage would be with this skills line.

    Recomputed per candidate rather than solved up front: adding a skill can only raise coverage,
    so the first line that clears the threshold is also the shortest one that does.
    """
    text = "\n".join([*body_lines, " · ".join(skills)])
    # `in_master_cv` is not part of the coverage sum (see `ats_coverage` — it weighs `weight` and
    # reads `matched`), so it isn't computed here. `build_ats` fills it in properly for the report.
    return ats_coverage(
        [
            AtsKeyword(term=k.term, weight=k.weight, matched=text_has_term(text, k.term), in_master_cv=True)
            for k in evidence.keywords
        ]
    )


def build_ats(
    doc: TailoredCV, *, cv: MasterCV, evidence: FitEvidence, cv_pages: int, job: Job, letter: CoverLetter | None = None
) -> AtsReport:
    text = tailored_text(doc, cv)
    master = master_cv_text(cv)
    letter_text = "\n".join(p.text for p in letter.paragraphs) if letter else None
    keywords = [
        AtsKeyword(
            term=k.term,
            weight=k.weight,
            matched=text_has_term(text, k.term),
            in_master_cv=text_has_term(master, k.term),
            category=k.category,
            in_letter=text_has_term(letter_text, k.term) if letter_text is not None else None,
        )
        for k in evidence.keywords
    ]
    pages = estimate_pages(doc, cv)
    fits = pages <= cv_pages
    page_msg = (
        f"About {cv_pages} page{'s' if cv_pages > 1 else ''}, estimated"
        if fits
        else f"About {_num(pages)} pages, estimated — trim to {cv_pages}"
    )
    file_name = cv_file_name(cv, job.company)
    return AtsReport(
        keywords=keywords,
        coverage=ats_coverage(keywords),
        backed_coverage=ats_coverage([k for k in keywords if k.in_master_cv]),
        # Over the backed keywords only, because those are the only ones the letter is allowed
        # to use (`nodes.letter_plan`): a letter is not short of a keyword it was told never to claim.
        # Nothing backed means nothing to cover, which is no measurement rather than 0%.
        letter_coverage=(
            ats_coverage([k.model_copy(update={"matched": bool(k.in_letter)}) for k in keywords if k.in_master_cv])
            if letter_text is not None and any(k.in_master_cv for k in keywords)
            else None
        ),
        pages=pages,
        # Only what this module can actually measure. It used to also report a single column, an
        # extractable file and no tables as passed — written as `pass_=True`, for a PDF that did
        # not exist yet. The rendered file is linted where it is rendered (`export/lint.ts`), and
        # the review rail shows that result in place of these once it has it.
        format_checks=[
            FormatCheck(id="pages", pass_=fits, message=page_msg),
            FormatCheck(
                id="file_name",
                pass_=bool(FILE_NAME.fullmatch(file_name)),
                message=file_name if FILE_NAME.fullmatch(file_name) else f"{file_name} isn't First-Last-CV-Company.pdf",
            ),
        ],
    )


def _num(n: float) -> str:
    """Numbers as JavaScript prints them: 2.0 → "2", 1.3 → "1.3"."""
    return str(int(n)) if float(n).is_integer() else str(n)
