"""A CV read without a model (CV-03).

Most of a CV is not ambiguous. Section headings are a closed vocabulary, date ranges are a
regex, bullets are marked with a bullet character, and skills are a comma-separated list under
a heading that says "Skills". A model was being asked to transcribe all of that — thousands of
tokens in and nearly as many back out, on the one document every later search is built on.

So this runs first, and the model only sees what this could not read. What makes that safe is
CV-03 itself: the result is a *proposal* the person confirms before anything is saved, so the
bar here is "right often enough to be worth correcting", not "right always". What this must
never do is quietly produce a confident wrong answer — hence `decline`, below.

What this deliberately does not attempt:

  * **A scanned PDF.** There is no text to parse; `cv-upload.ts` already refuses those.
  * **Two-column layouts**, where extraction interleaves the columns before this ever sees
    them. The giveaway is a section that has content but yields no datable entries.
  * **Deciding which half of "Acme Corp | Senior Engineer" is the title** when neither half
    carries a role word or a company suffix. A guess is recorded as a guess and the person
    fixes it in the editor they are required to visit anyway.

When this declines, `store.parse_cv` falls back to the `cv.parse` model call exactly as before.
The fallback is the safety net, not the plan.
"""

import re
from dataclasses import dataclass, field

from ..schemas.llm_outputs import ParsedCv, ParsedEducation, ParsedLanguage, ParsedLink, ParsedRole

# A heading is a short line naming a section. Matched against the start of the line so that
# "Professional Experience" and "Work Experience (selected)" both land, but a bullet that
# happens to mention experience does not — bullets are filtered out before this is consulted.
HEADINGS: dict[str, tuple[str, ...]] = {
    "experience": (
        "experience", "employment", "work history", "career history", "professional background",
        "work experience", "professional experience", "employment history", "relevant experience",
    ),
    "education": ("education", "academic", "qualifications", "academic background"),
    "projects": ("projects", "personal projects", "portfolio", "side projects", "selected projects"),
    "skills": ("skills", "technical skills", "technologies", "competencies", "core skills", "tools"),
    "summary": ("summary", "profile", "objective", "about me", "about", "personal statement"),
    "languages": ("languages", "spoken languages"),
    "links": ("links", "contact", "details", "personal details"),
    "interests": (
        "interests", "hobbies", "references", "awards", "certifications", "publications",
        "honours", "honors", "achievements", "activities", "extracurricular", "volunteering",
    ),
}  # fmt: skip

# Words that join two heading words without changing the heading: "Honours & Awards",
# "Education and Qualifications", "Areas of Interest".
_JOINERS = frozenset({"and", "or", "of", "the"})

# Long enough for "Professional Experience (most recent first)", short enough to exclude prose.
MAX_HEADING_CHARS = 48

BULLET = re.compile(r"^\s*[•·▪●■⁃∙*+–—-]\s+")

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_POINT = rf"(?:{_MONTH}\s+\d{{4}}|\d{{1,2}}[/.]\d{{4}}|\d{{4}}-\d{{1,2}}|\d{{4}})"
_OPEN = r"(?:present|current|now|ongoing|to date|date)"
DATE_RANGE = re.compile(rf"(?i)(?P<start>{_POINT})\s*(?:–|—|-|−|to|until|→)\s*(?P<end>{_OPEN}|{_POINT})")

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Loose on purpose: this is used to *remove* a phone number, and a missed one would travel.
PHONE = re.compile(r"(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?){2,5}\d{2,5}")
URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s,;|]+|\b[\w-]+\.(?:com|dev|io|org|net|uk|me|ai)/[^\s,;|]+")

# Words that mark a fragment as a job title rather than an employer, and the reverse. Neither
# list needs to be complete: a fragment matching neither is what `ambiguous` records.
ROLE_WORDS = frozenset({
    "engineer", "developer", "programmer", "analyst", "scientist", "manager", "director",
    "intern", "internship", "consultant", "designer", "architect", "administrator",
    "specialist", "lead", "head", "officer", "assistant", "associate", "researcher",
    "technician", "apprentice", "apprenticeship", "trainee", "graduate", "founder",
    "freelance", "teacher", "tutor", "ambassador", "volunteer", "president", "treasurer",
    "secretary", "coordinator", "supervisor", "executive", "placement", "contractor",
    "steward", "mentor", "advisor",
})  # fmt: skip
ORG_WORDS = frozenset({
    "ltd", "limited", "inc", "llc", "llp", "plc", "gmbh", "corp", "corporation", "co",
    "company", "technologies", "tech", "labs", "laboratories", "group", "solutions",
    "systems", "university", "college", "school", "institute", "academy", "bank",
    "consulting", "partners", "foundation", "trust", "society", "club", "council", "nhs",
    "bbc", "services", "media", "studio", "agency", "ventures", "capital", "holdings",
    "industries",
})  # fmt: skip

# A short label and a colon ("Relevant Modules:", "Thesis:") introduces a note about the
# entry above, never a new entry.
LABELLED = re.compile(r"^[A-Za-z][\w\s&/-]{0,24}:")

# "Senior Engineer, Acme Corp | London" — any of these split a header into its parts.
SEPARATOR = re.compile(r"\s*(?:[|•·]|—|–|\s+-\s+|\sat\s|,)\s*", re.I)


@dataclass
class Extraction:
    """What was read, or why it wasn't.

    `cv` and `decline` are exclusive: a declined extraction has no CV, and the caller runs the
    model. `ambiguous` is not a decline — it names the headers where the title/employer split
    was a guess, so the reason a person is correcting something is recoverable from the logs.
    """

    cv: ParsedCv | None = None
    decline: str | None = None
    ambiguous: list[str] = field(default_factory=list)


def _heading(line: str) -> str | None:
    """The section a line names, if it names one."""
    text = line.strip().rstrip(":").strip()
    if not text or len(text) > MAX_HEADING_CHARS or BULLET.match(line):
        return None
    # "Languages: Python, Java, Go" is a skills line, not the Languages heading. A heading may
    # end in a colon but never carries content after one — without this, a CV that labels its
    # skill rows loses the whole Skills section to whichever label came first.
    if LABELLED.match(line.strip()) and line.strip()[line.index(":") + 1 :].strip():
        return None
    # A heading with a date on it is an entry, not a heading ("Education 2021-2024").
    if DATE_RANGE.search(text):
        return None
    lowered = re.sub(r"[^a-z\s]", " ", text.lower()).strip()
    lowered = re.sub(r"\s+", " ", lowered)
    for section, names in HEADINGS.items():
        if any(lowered == name or lowered.startswith(name + " ") for name in names):
            return section
    # "Honours & Awards" names a section without matching any phrase above: it is two heading
    # words joined. A heading built only out of its own section's vocabulary is still that
    # heading, and missing one is expensive — everything under it lands in the section before,
    # which is how an awards list became three projects.
    words = [w for w in lowered.split() if w not in _JOINERS]
    if words:
        for section, names in HEADINGS.items():
            vocabulary = {word for name in names for word in name.split()}
            if all(word in vocabulary for word in words):
                return section
    return None


def _sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """The lines before the first heading, and the lines under each heading.

    A section seen twice is appended to rather than replaced: "Experience" and "Further
    Experience" are one list as far as everything downstream is concerned.
    """
    preamble: list[str] = []
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        section = _heading(line)
        if section:
            current = section
            found.setdefault(section, [])
            continue
        (found[current] if current else preamble).append(line)
    return preamble, found


def _strip_contacts(text: str) -> str:
    """CV-06/PRIV-01: an email, phone or street number never becomes part of a name or a city.

    This is the same instrument as `pii.scrub_contacts` and for the same reason, applied here
    because these fragments are read *into* stored fields rather than sent anywhere.
    """
    text = EMAIL.sub(" ", text)
    text = URL.sub(" ", text)
    text = PHONE.sub(" ", text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,;|•·-")


def _looks_like_name(line: str) -> bool:
    """Two to four capitalised words, no digits, no comma.

    The comma is what keeps "London, United Kingdom" from being read as a person — it passes
    every other test here, and it sits exactly where a name sits.
    """
    text = _strip_contacts(line)
    if not text or "," in text or any(ch.isdigit() for ch in text):
        return False
    words = text.split()
    return 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words if w[:1].isalpha())


def _header_like(text: str) -> bool:
    """Whether a line could be an entry's header rather than a bullet someone left unmarked.

    A header is short and is not a sentence. This is the only thing standing between "Added
    integration tests to the payments pipeline." and being read as the title of the next job,
    because the lookahead alone cannot tell them apart: both sit two lines above a date.
    """
    stripped = text.rstrip()
    # The length is a backstop against a paragraph, not the real test — project titles run
    # long ("High-Availability Distributed Key-Value Store – Python, Raft Consensus"). The
    # punctuation is what does the work: a bullet is a sentence and a header is not.
    return len(stripped) <= 120 and not stripped.endswith((".", ";", "!", "?"))


def _person(preamble: list[str]) -> tuple[str, str, str | None, list[ParsedLink]]:
    """Name, location, headline and links, from the block above the first heading."""
    name = next((_strip_contacts(line) for line in preamble if _looks_like_name(line)), "")

    links: list[ParsedLink] = []
    for line in preamble:
        for url in URL.findall(line):
            clean = url.rstrip(".,;)")
            label = "LinkedIn" if "linkedin" in clean.lower() else "GitHub" if "github" in clean.lower() else "Website"
            if not any(x.url == clean for x in links):
                links.append(ParsedLink(label=label, url=clean))

    # A location is a line with a comma and no digits that isn't the name — "London, United
    # Kingdom". Left empty rather than guessed when nothing fits; the person fills it in, and
    # §5.7 treats an empty location as "no location filter" rather than as a wrong one.
    location = ""
    for line in preamble:
        text = _strip_contacts(line)
        if text and text != name and "," in text and not any(ch.isdigit() for ch in text) and len(text) <= 60:
            location = text
            break

    # Whatever is left in the preamble once the name, the location and the contact details are
    # accounted for is the one-line pitch under the name. A CV that doesn't have one leaves
    # nothing behind here, which is why this is a leftover rather than a search.
    headline = None
    for line in preamble:
        text = _strip_contacts(line)
        if text and text not in (name, location) and len(text.split()) >= 3 and not _looks_like_name(line):
            headline = text
            break
    return name, location, headline, links


def _split_header(header: str) -> tuple[str, str, str | None, bool]:
    """`(title, org, location, guessed)` from "Senior Engineer, Acme Corp | London"."""
    parts = [p.strip(" .") for p in SEPARATOR.split(header) if p.strip(" .")]
    if not parts:
        return "", "", None, False
    if len(parts) == 1:
        # Nothing to split on: "Monzo Bank" above a date range is either the employer or the
        # job title and the line does not say which. Both fields are required by the contract,
        # so both get the text and the header is flagged — the person clears one of them in the
        # editor. Dropping the entry instead would lose a whole job silently, which is worse.
        return parts[0], parts[0], None, True

    def role_score(part: str) -> int:
        return sum(word in ROLE_WORDS for word in re.findall(r"[a-z]+", part.lower()))

    def org_score(part: str) -> int:
        return sum(word in ORG_WORDS for word in re.findall(r"[a-z]+", part.lower()))

    location = None
    if len(parts) >= 3:
        tail = parts[-1]
        if len(tail) <= 30 and len(tail.split()) <= 3 and not role_score(tail) and not org_score(tail):
            location, parts = tail, parts[:-1]

    head, rest = parts[0], " ".join(parts[1:])
    # The usual order is "Title, Employer". It is only overturned on evidence: a role word on
    # the second fragment and none on the first, or a company suffix on the first.
    flip = (role_score(rest) > role_score(head)) or (org_score(head) > org_score(rest))
    title, org = (rest, head) if flip else (head, rest)
    guessed = not role_score(title) and not org_score(org)
    return title, org, location, guessed


def _split_project_header(header: str) -> tuple[str, str, str | None, bool]:
    """`(title, org, location, guessed)` for a project, which reads the other way round.

    A job is written "Title, Employer"; a project is written "Name — what it is". The name is
    the `org` because that is what `cv_build` builds the id from (`prj-ferry`), and a project
    with no description is its own description rather than a blank.
    """
    parts = [p.strip(" .") for p in SEPARATOR.split(header) if p.strip(" .")]
    if not parts:
        return "", "", None, False
    name = parts[0]
    return (" ".join(parts[1:]) or name), name, None, False


def _entries(lines: list[str], ambiguous: list[str], *, project: bool = False, dateless: bool = False) -> list[ParsedRole]:
    """Roles or projects, grouped by the date range that heads each one.

    A date range is what marks a new entry: lines above it are its header, bullets below it are
    its bullets. The one genuinely undecidable line is an unmarked one in the middle — plenty of
    CVs write bullets as plain text, and the next entry's title is also plain text. It is read
    as a *header* when a date range follows it closely with no bullet in between, and as a
    bullet otherwise, which is the order a CV actually puts them in.
    """
    entries: list[ParsedRole] = []
    pending: list[str] = []
    body = [(line, line.strip()) for line in lines]
    kept = [i for i, (_, text) in enumerate(body) if text]

    def heads_an_entry(at: int) -> bool:
        """Whether the next line or two turn this into an entry header rather than a bullet."""
        for index in kept[kept.index(at) + 1 :][:2]:
            line, text = body[index]
            if BULLET.match(line):
                return False
            if DATE_RANGE.search(text):
                return True
        return False

    split = _split_project_header if project else _split_header

    def add_entry(header: str, start: str, end: str, *, require_split: bool = False) -> bool:
        title, org, location, guessed = split(header)
        if require_split and (guessed or LABELLED.match(header)):
            # A line under a heading is not automatically an entry. "First Class Honours
            # expected" and "Relevant Modules: Advanced Algorithms, …" sit under Education and
            # are notes on the degree above; without this they become degrees of their own —
            # and the second one splits on its commas convincingly enough to look like one.
            return False
        if guessed and header:
            ambiguous.append(header)
        entries.append(ParsedRole(title=title, org=org, location=location, start=start, end=end, bullets=[]))
        return True

    def absorb(text: str) -> bool:
        """Fold a second header line into the entry above it, if that entry wants one.

        The layout: the employer or institution on one line with the dates right-aligned onto
        it, and the job title or degree underneath. The first line splits into nothing — one
        fragment doing duty as both title and org — which is exactly the state this repairs.
        """
        last = entries[-1]
        if last.bullets or last.title != last.org:
            return False
        merged = f"{last.org}, {text}"
        title, org, location, guessed = split(merged)
        if guessed:
            return False
        if ambiguous and ambiguous[-1] == last.org:
            ambiguous.pop()
        last.title, last.org, last.location = title, org, location or last.location
        return True

    def add_text(text: str) -> None:
        """An unmarked line: a bullet, or the tail of the one above it.

        A PDF wraps a long bullet onto a second line, and that line arrives here looking like a
        bullet of its own ("**22%**.", "system."). A bullet left mid-sentence is the giveaway —
        if the one above does not end in sentence punctuation, this finishes it.
        """
        last = entries[-1].bullets[-1] if entries[-1].bullets else ""
        if last and not last.rstrip().endswith((".", "!", "?", ":", ";")):
            entries[-1].bullets[-1] = f"{last.rstrip()} {text.lstrip()}"
        else:
            entries[-1].bullets.append(text)

    for index in kept:
        line, text = body[index]

        if BULLET.match(line):
            if entries:
                # A header buffer that never found a date was never a header.
                entries[-1].bullets.extend(pending)
                entries[-1].bullets.append(BULLET.sub("", line).strip())
            pending = []
            continue

        if found := DATE_RANGE.search(text):
            add_entry(
                " ".join([*pending, DATE_RANGE.sub("", text).strip(" ,;|–—-")]).strip(),
                found.group("start"),
                found.group("end"),
            )
            pending = []
            continue

        if entries and _header_like(text) and not heads_an_entry(index) and absorb(text):
            continue
        if entries and not _header_like(text):
            add_text(text)  # a sentence: a bullet whose marker didn't survive, or its tail
        elif heads_an_entry(index) or (not entries and not dateless):
            pending.append(text)
            del pending[:-3]  # a header is a line or two; more means the buffer is drifting
        elif dateless:
            # No date is coming, and this section allows entries without one — a degree and a
            # side project are both routinely written with no years at all, and sending a whole
            # CV to a model over one undated line would be the tail wagging the dog. The blanks
            # are visible in the editor, which is where a missing date gets filled in anyway.
            header = " ".join([*pending, text]).strip()
            pending = []
            if not add_entry(header, "", "", require_split=not project) and entries:
                add_text(text)
        else:
            add_text(text)

    if dateless and pending:
        add_entry(" ".join(pending).strip(), "", "", require_split=not project)

    return entries


def _education(lines: list[str], ambiguous: list[str]) -> list[ParsedEducation]:
    """Education entries. Same shape as a role, with degree and institution in place of title
    and employer — and the same "usually in this order" rule, since a CV writes the degree
    first about as reliably as it writes the job title first."""
    out: list[ParsedEducation] = []
    for role in _entries(lines, ambiguous, dateless=True):
        degree = role.title or role.org
        institution = role.org if role.title else ""
        # "BSc Computer Science, University of Leeds" splits the usual way round, but a line
        # naming a university on the left is the one case worth correcting here.
        if institution and any(word in ORG_WORDS for word in re.findall(r"[a-z]+", degree.lower())):
            degree, institution = institution, degree
        out.append(ParsedEducation(degree=degree, institution=institution, start=role.start, end=role.end))
    return out


def _terms(lines: list[str]) -> list[str]:
    """Skills, from however the CV lists them: one per line, comma-separated, or both.

    A line with a leading label keeps only what follows it ("Languages: Python, Go"), because
    the label is a category of the CV's own invention and not a skill anyone searches for.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        text = BULLET.sub("", line).strip()
        if not text:
            continue
        if ":" in text:
            text = text.split(":", 1)[1]
        for term in re.split(r"[,;|•·/]|\s{2,}", text):
            clean = term.strip(" .")
            # A sentence is a claim about the person, not a skill. Skills are short.
            if clean and len(clean) <= 30 and len(clean.split()) <= 4 and clean.casefold() not in seen:
                seen.add(clean.casefold())
                out.append(clean)
    return out[:40]  # ParsedCv caps at 40


def _languages(lines: list[str]) -> list[ParsedLanguage]:
    """Spoken languages. "German (fluent)" and "German — fluent" both arrive here."""
    out: list[ParsedLanguage] = []
    for line in lines:
        text = BULLET.sub("", line).strip(" .")
        if not text:
            continue
        found = re.match(r"(?i)^([a-z\s]+?)\s*[(—–:-]\s*([a-z\s\d()]+?)\)?\s*$", text)
        if found:
            out.append(ParsedLanguage(name=found.group(1).strip(), level=found.group(2).strip()))
        elif len(text.split()) == 1:
            out.append(ParsedLanguage(name=text, level=""))
    return out


def extract(text: str) -> Extraction:
    """Read a CV, or decline and say why.

    Declining is the important half. Every condition below is a case where carrying on would
    produce a confident, wrong CV — which is worse than the model call it costs to avoid, both
    for the person reviewing it and for every search built on it afterwards.
    """
    lines = text.splitlines()
    preamble, sections = _sections(lines)

    has_content = {k: any(line.strip() for line in v) for k, v in sections.items()}
    if not any(has_content.get(k) for k in ("experience", "projects", "education")):
        return Extraction(decline="no experience, projects or education heading")

    ambiguous: list[str] = []
    experience = _entries(sections.get("experience", []), ambiguous)
    projects = _entries(sections.get("projects", []), ambiguous, project=True, dateless=True)
    education = _education(sections.get("education", []), ambiguous)

    # A section with content that yielded nothing datable is the signature of a layout this
    # cannot read — most often two columns interleaved by the PDF extractor. Handing over a CV
    # with its Experience section silently missing is exactly the confident-and-wrong case.
    for name, parsed in (("experience", experience), ("projects", projects), ("education", education)):
        if has_content.get(name) and not parsed:
            return Extraction(decline=f"{name} section had content but no datable entries")

    if not (experience or projects or education):
        return Extraction(decline="no entries found")

    # A degree and the institution that awarded it are the two facts education exists to carry,
    # and a header that didn't split leaves both holding the same text. Unlike an ambiguous job
    # title — one line a person can fix — this is the whole entry being wrong, and it is the
    # signature of a layout that writes the institution and the degree on separate lines with
    # the dates right-aligned onto the first. That one goes to the model.
    if any(entry.degree == entry.institution for entry in education):
        return Extraction(decline="an education entry's degree and institution could not be told apart")

    name, location, headline, links = _person(preamble)
    if not name:
        return Extraction(decline="no name found above the first heading")

    summary = " ".join(line.strip() for line in sections.get("summary", []) if line.strip())
    return Extraction(
        cv=ParsedCv(
            name=name,
            headline=headline,
            location=location,
            links=links,
            summary=summary or None,
            experience=experience,
            projects=projects,
            education=education,
            skills=_terms(sections.get("skills", [])),
            languages=_languages(sections.get("languages", [])),
        ),
        ambiguous=ambiguous,
    )
