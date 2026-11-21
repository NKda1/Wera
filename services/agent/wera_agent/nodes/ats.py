"""ATS check (SPEC §8.3, ATSC-01…05): coverage and format checks, computed in code."""

import math
import re
import unicodedata

from ..domain import vocab
from ..domain.degrees import looks_like_degree
from ..domain.filters import title_tokens
from ..domain.scoring import ats_coverage, js_round
from ..domain.vocab import (
    education_text,
    group_key,
    master_cv_text,
    normalise_text,
    not_a_skill,
    role_header,
    skill_text,
    text_has_term,
)
from ..schemas.cv import MasterCV
from ..schemas.jobs import FitEvidence, Job
from ..schemas.pack import (
    AtsCompare,
    AtsKeyword,
    AtsPart,
    AtsReport,
    AtsSpan,
    Cited,
    CompareKeyword,
    CoverLetter,
    FormatCheck,
    TailoredCV,
)

# Roughly two lines of skills per page, at `estimate_pages`' 95 characters a line. A character
# budget rather than a count, because ten long skill names and ten short ones are not the same
# amount of page.
SKILLS_LINE_CHARS = 190
# And a count as well (20 Nov): the JobLeads review of 19 Nov asked for 8–15 skills, tailored per
# job, and two lines of short names held up to 25. More than 15 reads as a list of everything,
# which is the opposite of tailored. It was 25, and only in the score (`ats_score`'s length part);
# now `reconcile_skills` stops at it too, so WERA no longer writes a line it then marks down.
MAX_SKILLS = 15


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
    """As many skills as the line has room for, and never more than `MAX_SKILLS`, in the order
    given.

    Order is therefore load-bearing: whatever `reconcile_skills` puts first is what survives.
    """
    budget = cv_pages * SKILLS_LINE_CHARS
    out: list[str] = []
    length = 0
    for name in names:
        extra = len(name) + (3 if out else 0)  # the " · " separator
        if out and (length + extra > budget or len(out) >= MAX_SKILLS):
            break
        out.append(name)
        length += extra
    return out


# The most lines a role shows (`prompts.CV_TAILOR`: "at most 3 per role").
LINES_PER_ROLE = 3


def place_required(doc: TailoredCV, *, cv: MasterCV, evidence: FitEvidence) -> TailoredCV:
    """ATSC-07's lift: a required keyword the CV backs in a line the tailor left out is shown.

    A parser and a recruiter both weigh a skill in a line of work above one in the skills list,
    and `cv.tailor` picks lines for strength, not for the post's checklist. So, after it, in code:
    for each required keyword the shown lines don't carry, the first left-out line of a shown
    role that does is brought in — into a free place, or in place of that role's last (weakest)
    line when the last carries no required keyword of its own. Lines are copied from the master
    verbatim, so nothing here writes a claim, and the page budget is unchanged.
    """
    owner = {b.id: r.id for r in [*cv.experience, *cv.projects] for b in r.bullets}
    master = {b.id: b.text for r in [*cv.experience, *cv.projects] for b in r.bullets}
    required = [k.term for k in evidence.keywords if k.weight == "required"]
    items = {i.role_id: i for sec in doc.sections for i in sec.items}

    def shown_text() -> str:
        return "\n".join(b.text for i in items.values() for b in i.bullets)

    for term in required:
        if text_has_term(shown_text(), term):
            continue
        shown_ids = {sid for i in items.values() for b in i.bullets for sid in b.source_ids}
        for line_id, text in master.items():
            item = items.get(owner[line_id])
            if item is None or line_id in shown_ids or not text_has_term(text, term):
                continue
            line = Cited(text=text, source_ids=[line_id])
            if len(item.bullets) < LINES_PER_ROLE:
                item.bullets.append(line)
            elif not any(text_has_term(item.bullets[-1].text, t) for t in required):
                doc.omitted_bullet_ids = [*doc.omitted_bullet_ids, *item.bullets[-1].source_ids]
                item.bullets[-1] = line
            else:
                continue
            doc.omitted_bullet_ids = [i for i in doc.omitted_bullet_ids if i != line_id]
            break
    return doc


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

    def add(name: str, shown: str | None = None) -> None:
        """`shown`, when given, is how the line spells it: the post's term, the CV's in brackets."""
        if name.casefold() not in seen:
            seen.add(name.casefold())
            chosen.append(shown or name)

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
            # The post's spelling first when the CV spells the same skill differently ("Postgres"
            # for a post's "PostgreSQL"): a parser looks for the post's word. The CV's own stays
            # beside it, so the line still says what the applicant wrote (14 Nov).
            respelled = _occurrences(name, keyword.term) == 0 and not _occurrences(keyword.term, name)
            add(name, f"{keyword.term} ({name})" if respelled else None)

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
    doc: TailoredCV,
    *,
    cv: MasterCV,
    evidence: FitEvidence,
    cv_pages: int,
    job: Job,
    letter: CoverLetter | None = None,
    text: str | None = None,
    pages: int | None = None,
) -> AtsReport:
    """`text` and `pages` are the CV file's when it has been rendered (EXP-01): everything it
    prints, and the page count of the PDF itself. Without them the text is the tailored CV's and
    the page count an estimate from line lengths, which is what a pack has while it is drafted."""
    text = text if text is not None else tailored_text(doc, cv)
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
    if pages is not None:
        # `export/lint.ts`'s wording, since it is the same count of the same file.
        fits = pages <= cv_pages
        page_msg = f"{pages} page{'' if pages == 1 else 's'}" if fits else f"{pages} pages, over the {cv_pages}-page limit"
        measured = float(pages)
    else:
        measured = estimate_pages(doc, cv)
        fits = measured <= cv_pages
        page_msg = (
            f"About {cv_pages} page{'s' if cv_pages > 1 else ''}, estimated"
            if fits
            else f"About {_num(measured)} pages, estimated — trim to {cv_pages}"
        )
    file_name = cv_file_name(cv, job.company)
    score, best, parts = ats_score(doc, cv, keywords, job=job, fits=fits, file_ok=bool(FILE_NAME.fullmatch(file_name)), text=text)
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
        pages=measured,
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
        score=score,
        best_score=best,
        parts=parts,
        suggested_skills=suggested_skills(cv, keywords),
    )


def suggested_skills(cv: MasterCV, keywords: list[AtsKeyword]) -> list[str]:
    """ATSC-07: keywords the CV backs in a line but doesn't list as a skill — one per skill, however
    the post spelled it ("REST APIs" and "RESTful APIs" were two suggestions on 14 Nov), and never a
    field of study or a trait ("Computer Science", "Engineering")."""
    out: list[str] = []
    seen: set[str] = set()
    for k in keywords:
        if not k.in_master_cv or _skill_for_term(cv, k.term) is not None:
            continue
        key = group_key(k.term)
        if not_a_skill(k.term) or looks_like_degree(k.term) or key in seen:
            continue
        seen.add(key)
        out.append(k.term)
    return out


# ---------------------------------------------------------------------------
# ATSC-07 (14 Nov): the ATS score — five parts, in code, modelled on how a parser and a recruiter's
# keyword search read a CV. Coverage alone (what this module measured before) says whether the
# words are there; a recruiter also asks where they are, whether the CV is for this role, and
# whether it parses. Each part says why it scored what it did.

PART_MAX = {"keywords": 45.0, "evidence": 20.0, "alignment": 15.0, "structure": 10.0, "length": 10.0}
# A methodology is softer evidence than a named skill or tool: a parser matches it, a recruiter
# discounts it. Required still counts double, as in `ats_coverage`.
CATEGORY_WEIGHT = {"methodology": 0.75}
# More than this and a term reads as stuffing to the human who sees the CV after the parser.
MAX_REPEATS = 4


def _weighted(keywords: list[AtsKeyword], hit) -> float:  # noqa: ANN001 — a predicate on a keyword
    total = got = 0.0
    for k in keywords:
        w = (2.0 if k.weight == "required" else 1.0) * CATEGORY_WEIGHT.get(k.category or "", 1.0)
        total += w
        got += w if hit(k) else 0.0
    return 1.0 if total == 0 else got / total


def _occurrences(text: str, term: str) -> int:
    return len(re.findall(rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])", text.lower()))


def title_grade(title: str, headline: str | None, summary: str) -> tuple[float | None, str]:
    """How plainly the CV says it is for this role, 0–100, and why (20 Nov).

    Graded as resume-matcher's `compute_ats_score` grades it (Apache-2.0, see NOTICE): 100 when
    the role is in the headline, 75 when it is in the summary, otherwise half the share of the
    role's words that appear in either. "The role" is what the title is about (`title_tokens`:
    no level, no brackets, one spelling per word), so "Senior Data Analyst (Hybrid)" is in a
    headline that reads "Data analyst".

    It was the share of words alone, which put a headline naming the role level with a summary
    that happened to use its words in passing — a recruiter's eye goes to the headline first.
    None when the title has no words to look for: there is nothing to measure.
    """
    wanted = sorted(title_tokens(title))
    if not wanted:
        return None, "The job title has no words to look for"

    def has(text: str, word: str) -> bool:
        # Read the way the title was: "ML Engineer" in a headline is "machine learning engineer"
        # to `title_tokens`, so a headline repeating the post's title has every word it wants.
        return word in title_tokens(text) or text_has_term(text, word)

    role = " ".join(wanted)
    if headline and all(has(headline, w) for w in wanted):
        return 100.0, f"The role ({role}) is in your headline"
    if summary and all(has(summary, w) for w in wanted):
        return 75.0, f"The role ({role}) is in your summary, not your headline"
    top = f"{headline or ''}\n{summary}"
    found = [w for w in wanted if has(top, w)]
    share = len(found) / len(wanted)
    if not found:
        return 0.0, f"None of the role's words ({', '.join(wanted)}) in your headline or summary"
    return 50.0 * share, f"{len(found)} of {len(wanted)} of the role's words ({', '.join(wanted)}) in your headline or summary"


def ats_score(
    doc: TailoredCV, cv: MasterCV, keywords: list[AtsKeyword], *, job: Job, fits: bool, file_ok: bool, text: str | None = None
) -> tuple[float, float, list[AtsPart]]:
    """The score, the best this CV could score with every keyword it backs placed, and the parts.

    20 Nov: a part that can't be measured is None, and the score is the measured parts' points
    over their own weight, scaled to 100 (resume-matcher's `compute_ats_score`, Apache-2.0). A
    post that names no keywords used to give the keywords part full marks — 45 points for a
    measurement nobody made, on exactly the posts where WERA knows least. Each part's `max` is
    its weight rescaled, so the card's "points / max" still adds up to the score and to 100.
    """
    bullets = {i.role_id: [b.text for b in i.bullets] for s in doc.sections for i in s.items}
    bullet_text = "\n".join(t for texts in bullets.values() for t in texts)
    latest = max(cv.experience, key=lambda r: r.start or "", default=None)
    latest_text = "\n".join(bullets.get(latest.id, [])) if latest else ""

    keyword_share: float | None = _weighted(keywords, lambda k: k.matched) if keywords else None
    best_share = _weighted(keywords, lambda k: k.matched or k.in_master_cv) if keywords else None
    shown = [k for k in keywords if k.matched and k.weight == "required"] or [k for k in keywords if k.matched]
    evidence: float | None
    if not keywords:
        evidence, evidence_why = None, "Not measured: the post names no keywords to show in your work."
    elif not shown:
        evidence, evidence_why = 0.0, "None of the post's keywords is on the CV yet."
    else:
        in_lines = sum(text_has_term(bullet_text, k.term) for k in shown) / len(shown)
        in_latest = sum(text_has_term(latest_text, k.term) for k in shown) / len(shown)
        evidence = 0.75 * in_lines + 0.25 * in_latest
        evidence_why = f"{round(in_lines * len(shown))} of {len(shown)} matched {'required ' if shown[0].weight == 'required' else ''}keywords shown in a line of work, not only the skills list"

    has_summary = bool(doc.summary.text.strip())
    grade, grade_why = title_grade(job.title, cv.person.headline, doc.summary.text)
    # The title is 10 of this part's 15 and a summary the other 5. A title with no words leaves
    # the summary to measure, so the part is the summary alone rather than unmeasured.
    alignment = (10 * grade / 100 + (5 if has_summary else 0)) / 15 if grade is not None else (1.0 if has_summary else 0.0)
    alignment_why = f"{grade_why}; {'a' if has_summary else 'no'} summary"

    sections = {"summary": has_summary, "experience": any(s.kind == "experience" for s in doc.sections),
                "education": bool(cv.education), "skills": bool(doc.skills)}  # fmt: skip
    structure = (2 * sum(sections.values()) + (2 if file_ok else 0)) / 10
    missing = [name for name, ok in sections.items() if not ok]
    structure_why = ("Every section present" if not missing else f"Missing: {', '.join(missing)}") + (
        "" if file_ok else "; file name not First-Last-CV-Company"
    )

    text = text if text is not None else tailored_text(doc, cv)
    stuffed = [k.term for k in keywords if _occurrences(text, k.term) > MAX_REPEATS]
    length = ((5 if fits else 0) + (3 if not stuffed else 0) + (2 if len(doc.skills) <= MAX_SKILLS else 0)) / 10
    length_why = ", ".join(
        [("fits the page budget" if fits else "over the page budget"),
         (f"{', '.join(stuffed)} repeated more than {MAX_REPEATS} times" if stuffed else "no keyword repeated to excess"),
         (f"{len(doc.skills)} skills listed" + ("" if len(doc.skills) <= MAX_SKILLS else f", more than {MAX_SKILLS}"))]
    )  # fmt: skip

    shares: dict[str, float | None] = {"keywords": keyword_share, "evidence": evidence, "alignment": alignment, "structure": structure, "length": length}  # fmt: skip
    labels = {"keywords": "Keywords", "evidence": "Shown in your work", "alignment": "Aimed at this role", "structure": "Sections and format", "length": "Length and density"}  # fmt: skip
    hits = sum(k.matched for k in keywords)
    whys = {
        "keywords": f"{hits} of {len(keywords)} of the post's keywords on the CV"
        if keywords
        else "Not measured: the post names no keywords.",
        "evidence": evidence_why,
        "alignment": alignment_why,
        "structure": structure_why,
        "length": length_why,
    }
    scored = sum(PART_MAX[pid] for pid, share in shares.items() if share is not None)
    scale = 100.0 / scored  # structure and length are always measured, so never zero
    weight = {pid: (PART_MAX[pid] * scale if shares[pid] is not None else 0.0) for pid in PART_MAX}
    parts = [
        AtsPart(
            id=pid,  # type: ignore[arg-type] — PART_MAX's keys are AtsPart's ids
            label=labels[pid],
            points=None if (share := shares[pid]) is None else js_round(weight[pid] * share * 10) / 10,
            max=js_round(weight[pid] * 10) / 10,
            detail=whys[pid],
        )
        for pid in PART_MAX
    ]
    score = js_round(sum(p.points or 0.0 for p in parts))
    best = score
    if keyword_share is not None and best_share is not None:
        best = js_round(score + weight["keywords"] * (best_share - keyword_share))
    return float(score), float(best), parts


# ---------------------------------------------------------------------------
# ATSC-08 (20 Nov): the job ad beside the CV, after resume-matcher's `jd-comparison-view`
# (Apache-2.0, see NOTICE). Each of the post's keywords is highlighted where it is, on both
# pages, by the matcher every number on the ATS card comes from.


def display_text(text: str) -> str:
    """A page as the side-by-side view shows it, and as `term_spans` reads it: one spelling per
    character (`vocab.normalise_text`) line by line, single spaces, no blank lines.

    Normalised before it is shown, rather than mapped back afterwards, because the matcher reads
    the normalised form: a span on this text is a span on what was matched. And it keeps
    `term_spans` exact — this text with its line breaks read as spaces *is* `normalise_text` of
    it, which is what `text_has_term` searches.
    """
    lines = (normalise_text(line) for line in unicodedata.normalize("NFKC", text).splitlines())
    return "\n".join(line for line in lines if line)


_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"\*\*|__|`")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_MD_BULLET = re.compile(r"^\s*[*+-]\s+", re.M)


def ad_text(markdown: str | None) -> str:
    """A post's description as plain text: Markdown's links, emphasis and heading marks taken
    out, list items kept as "• ". Every mark it removes is punctuation, which the matcher reads
    as a word boundary already, so no keyword is found or lost by it."""
    text = _MD_LINK.sub(r"\1", markdown or "")
    text = _MD_BULLET.sub("• ", _MD_HEADING.sub("", text))
    return display_text(_MD_MARKS.sub("", text))


def _searchable(text: str) -> tuple[str, list[int], list[int]]:
    """`text` as `text_has_term` searches it — lower case, one spelling per word — with where
    each of its characters came from: `[lo[i], hi[i])` in `text`.

    `_one_spelling` turns "modelling" into "modeling", so the searched text is not the shown
    text character for character, and a span found in one has to be carried back to the other.
    A respelt word maps as a whole, which is all a match can touch: the boundary keeps a match
    from starting or ending inside a word.
    """
    lowered = text.lower()
    if len(lowered) != len(text):  # "İ" lowers to two characters; per character keeps the count
        lowered = "".join(c.lower()[:1] for c in text)
    out: list[str] = []
    lo: list[int] = []
    hi: list[int] = []
    at = 0
    for m in vocab._SPELLING_RE.finditer(lowered):
        out.append(lowered[at : m.start()])
        lo.extend(range(at, m.start()))
        hi.extend(range(at + 1, m.start() + 1))
        new = vocab._SPELLINGS[m.group(1)]
        out.append(new)
        lo.extend([m.start()] * len(new))
        hi.extend([m.end()] * len(new))
        at = m.end()
    out.append(lowered[at:])
    lo.extend(range(at, len(lowered)))
    hi.extend(range(at + 1, len(lowered) + 1))
    return "".join(out), lo, hi


def term_spans(text: str, term: str) -> list[AtsSpan]:
    """Every place `vocab.text_has_term(text, term)` would match, as spans in `text`.

    The same aliases, in the same order of rules — ambiguous words only as the post wrote them,
    proper nouns only capitalised, everything else by `_alias_re` on the lower-cased text — so
    this finds a span exactly when `text_has_term` says the term is there. `text` is expected in
    `display_text`'s form; `tests/test_compare.py` holds the two to the same answers.
    """
    flat = text.replace("\n", " ")
    hay, lo, hi = _searchable(flat)
    own = set(vocab._reductions(vocab.normalise_term(term))[:2])
    found: set[tuple[int, int]] = set()
    for alias in vocab.aliases_for(term):
        if alias in vocab.AMBIGUOUS_ALIASES and alias not in own:
            continue
        if alias in vocab.CASE_SENSITIVE_ALIASES:
            # Its groups are the boundary characters either side; the word is between them.
            for m in vocab._proper_noun_re(alias).finditer(flat):
                found.add((m.end(1), m.start(2)))
        else:
            for m in vocab._alias_re(alias).finditer(hay):
                found.add((lo[m.start()], hi[m.end() - 1]))
    # One span per place: where aliases overlap ("rest api" inside "rest apis"), the longest.
    out: list[AtsSpan] = []
    for start, end in sorted(found, key=lambda p: (p[0], -p[1])):
        if out and start < out[-1].end:
            continue
        out.append(AtsSpan(start=start, end=end))
    return out


def compare(job: Job, keywords: list[AtsKeyword], cv_text: str, *, master: str) -> AtsCompare:
    """The side-by-side view of a pack: its ATS report's keywords, found on the ad, on `cv_text`
    (the text that report measures — the CV file's) and on `master` (`vocab.master_cv_text`).

    Each keyword's status is measured again here, by the spans themselves, rather than copied
    from the stored report — so a keyword is green exactly when it is highlighted on the CV. The
    two only differ when the matcher has learnt a spelling since the report was stored: the live
    check on 20 Nov found "Financial Modeling", "comps" and "PitchBook" unmatched on packs
    measured that morning, before their synonym groups existed. `stale` says so, and the card
    catches up on the pack's next edit (`Store._measure_ats`).
    """
    job_text = ad_text(job.description_md)
    cv_text = display_text(cv_text)
    rows: list[CompareKeyword] = []
    measured: list[AtsKeyword] = []
    for k in keywords:
        cv_spans = term_spans(cv_text, k.term)
        now = k.model_copy(update={"matched": bool(cv_spans), "in_master_cv": text_has_term(master, k.term)})
        measured.append(now)
        rows.append(
            CompareKeyword(
                term=k.term,
                weight=k.weight,
                status="matched" if now.matched else "in_cv" if now.in_master_cv else "missing",
                job_spans=term_spans(job_text, k.term),
                cv_spans=cv_spans,
            )
        )
    return AtsCompare(
        job_text=job_text,
        cv_text=cv_text,
        keywords=rows,
        coverage=ats_coverage(measured),
        potential_coverage=ats_coverage([k.model_copy(update={"matched": k.matched or k.in_master_cv}) for k in measured]),
        description_status=job.description_status,
        stale=any((a.matched, a.in_master_cv) != (b.matched, b.in_master_cv) for a, b in zip(keywords, measured, strict=True)),
    )


def _num(n: float) -> str:
    """Numbers as JavaScript prints them: 2.0 → "2", 1.3 → "1.3"."""
    return str(int(n)) if float(n).is_integer() else str(n)
