"""A job description laid out to read (18 Nov): key facts, then sections, with the boilerplate folded.

Descriptions arrive as plain text with newlines, from very different places: an ATS feed through
`html_to_text`, a board page, a job API. Underneath, nearly all of them are the same document —
an opening, what you'll do, what they want, what they offer, then the company and the legal
small print — written with different furniture. Measured on 2,067 stored jobs (18 Nov):

- **Headings** as ALL-CAPS lines ("WHERE YOU'LL CREATE IMPACT"), as bare title-case lines ("About
  the role"), as "Your responsibilities:", or glued to the first bullet ("Requirements- 2+ years").
- **Lists** as "- " items, as items with blank lines between them, or as one short paragraph per
  item under a heading (Lever's feed).
- **Leading "Label: value" lines** ("Salary: up to £200k base + bonus") that repeat the card.
- **Boilerplate** at the edges: the company's blurb first, the equal-opportunity and
  accommodation text last.

Everything here is code and every word shown is the employer's: nothing is rewritten, only laid
out. It has to work for every kind of job, not the software posts WERA is mostly tested on — a
nursing post's "Band: 5" and "Enhanced DBS check" are facts the same way a software post's
"Hybrid, 3 days a week in the office" is — so the vocabulary below is about how job posts are
written, never about any one field.
"""

import hashlib
import re

from ..schemas.jobs import DescriptionBlock, DescriptionSection, DescriptionView, JobFact

# Bump when the output changes, so a stored view is recomputed on its next read.
VERSION = 1

_BULLET = re.compile(r"^\s*(?:[-*•·◦▪●○■□➢➤►✓✔–—]|\d{1,2}[.)])\s+")
_ORDERED = re.compile(r"^\s*\d{1,2}[.)]\s+")
_MD_HEADING = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
# "Responsibilities- Build…", "Requirements: 2+ years…": a section heading glued to its first
# item. The dash has to touch the heading — "We hire outliers - people who…" is a sentence.
_GLUED = re.compile(r"^([A-Z][A-Za-z’' &/]{1,39}?[A-Za-z])(?:[-–]|\s*:)\s*(?=\S)(.+)$")

# Words that make a short line a heading. About how posts are organised, not about any field.
_HEADING_WORDS = re.compile(
    r"\b(about|role|responsibilit\w*|duties|requirement\w*|qualification\w*|skills?|experience|profile|"
    r"description|overview|summary|offer|benefits?|perks|package|compensation|rewards?|salary|pay|why|"
    r"what|who|how|you|your|we|our|us|team|company|organi[sz]ation|mission|culture|values|apply|application|"
    r"process|next steps|essential|desirable|criteria|specification|eligibility|key|main|purpose|"
    r"opportunity|day[- ]to[- ]day|impact|looking for|nice to have|bonus|ideal|candidate|position|job|"
    r"working|hours|location|contract|training|development|support|equal|diversity|inclusion|"
    r"accommodation|privacy|contact|notes?|important|additional|information|details)\b",
    re.I,
)

_KINDS: list[tuple[str, re.Pattern[str]]] = [
    (
        "legal",
        re.compile(
            r"equal (?:employment )?opportunit|diversity|inclusi|accommodation|eeo|privacy|data protection|disclaimer|e-verify|safeguarding statement|fraud",
            re.I,
        ),
    ),
    (
        "nice",
        re.compile(
            r"nice[- ]to[- ]have|bonus points|desirable|preferred|\bplus\b|ideally|advantage|good to have|extra credit", re.I
        ),
    ),
    (
        "requirements",
        re.compile(
            r"requirement|qualification|skills|experience|you have|you'?ll have|you bring|about you|looking for|essential|criteria|person spec|who you are|must[- ]have|what you(?:'ll)? need|need to succeed|sets you up|eligib|your profile|^profile$|competenc",
            re.I,
        ),
    ),
    (
        "benefits",
        re.compile(
            r"benefit|perks|we offer|what'?s in it|why join|why you'?ll|why work|compensation|package|rewards?|salary|^pay\b|what you get|thrive here",
            re.I,
        ),
    ),
    (
        "process",
        re.compile(
            r"how to apply|application process|interview|next steps|^contact|^apply|hiring process|recruitment process|selection process",
            re.I,
        ),
    ),
    (
        "role",
        re.compile(
            r"responsibilit|duties|the role|about the role|what you'?ll (?:be )?do|you will|successful (?:candidate|applicant) will|job overview|your role|day[- ]to[- ]day|you'?ll own|^you'?ll$|impact|key tasks|main purpose|job description|^overview$|role overview|the opportunity|the position|job summary|what the job|in this role|^summary$|role summary|the work",
            re.I,
        ),
    ),
    (
        "company",
        re.compile(
            r"about (?:us|the company|the organi[sz]ation|the firm|the practice|the trust|the school|the employer)|who we are|our (?:company|mission|story|values|culture)|^the company$|^company$|^overview of (?!the role)|what sets us apart",
            re.I,
        ),
    ),
]
_FOLDED = {"company", "legal", "process"}

# Paragraphs that are the small print wherever they sit, heading or not.
_SMALL_PRINT = re.compile(
    r"equal (?:employment )?opportunit|without regard to|protected (?:by law|characteristic|veteran)|"
    r"reasonable (?:accommodation|adjustment)|affirmative action|e-verify|"
    r"not (?:be )?(?:able|possible) to respond|high volume of applications|only successful (?:candidates|applicants)|"
    r"privacy (?:notice|policy)|data protection|recruitment agenc(?:y|ies)\b.*\b(?:cv|resume|fee)|"
    r"background check|will not discriminate|committed to (?:creating|building) a diverse|"
    r"we (?:welcome|encourage) applications from",
    re.I,
)

# Labels a post puts on a line of its own ("Salary: £32k", "Band: 5", "Closing date: 1 Dec").
_FACT_LABELS = {
    "salary": "Salary",
    "pay": "Salary",
    "rate": "Rate",
    "compensation": "Salary",
    "package": "Package",
    "location": "Location",
    "based": "Location",
    "base": "Location",
    "office": "Location",
    "job type": "Contract",
    "contract": "Contract",
    "contract type": "Contract",
    "employment type": "Contract",
    "type": "Contract",
    "duration": "Duration",
    "contract duration": "Duration",
    "contract length": "Duration",
    "hours": "Hours",
    "working hours": "Hours",
    "working pattern": "Working pattern",
    "schedule": "Hours",
    "shift": "Hours",
    "closing date": "Closing date",
    "deadline": "Closing date",
    "application deadline": "Closing date",
    "start date": "Start date",
    "start": "Start date",
    "reference": "Reference",
    "ref": "Reference",
    "job reference": "Reference",
    "vacancy reference": "Reference",
    "job id": "Reference",
    "department": "Department",
    "team": "Team",
    "division": "Department",
    "directorate": "Department",
    "grade": "Grade",
    "band": "Band",
    "level": "Level",
    "pay band": "Band",
    "pay scale": "Pay scale",
    "remote": "Working pattern",
    "hybrid": "Working pattern",
    "workplace type": "Working pattern",
    "flexibility": "Working pattern",
    "job flexibility": "Working pattern",
    "sector": "Sector",
    "industry": "Sector",
    "specialty": "Specialty",
    "speciality": "Specialty",
    "visa sponsorship": "Visa",
    "sponsorship": "Visa",
    "clearance": "Clearance",
    "security clearance": "Clearance",
}
_FACT_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z /&-]{1,28}?)\s*:\s*(\S.{0,140})$")

# Facts read out of the prose, each as the post words it. First match only.
_INFERRED: list[tuple[str, re.Pattern[str]]] = [
    (
        "Contract",
        re.compile(
            r"\b(permanent|fixed[- ]term(?: contract)?(?: (?:of|for) \d{1,2} months)?|\d{1,2}[- ]months?(?: fixed[- ]term)? contract|"
            r"temporary|part[- ]time|zero[- ]hours|internship|apprenticeship|graduate (?:scheme|programme|program)|"
            r"locum|bank (?:staff|role|position)|freelance|contract(?:or)? (?:role|position))\b",
            re.I,
        ),
    ),
    (
        "Experience",
        # "7+ years of finance experience", "2 to 6 years of post-graduation professional experience".
        re.compile(
            r"\b((?:at least |minimum (?:of )?|over )?\d{1,2}\+?(?:\s*(?:-|–|to)\s*\d{1,2}\+?)?\s+years?['’]?\s+(?:of\s+)?(?:[\w/-]+\s+){0,4}?experience)\b",
            re.I,
        ),
    ),
    (
        "Working pattern",
        re.compile(
            r"\b((?:hybrid[^.\n;]{0,40}?)?\d (?:days?|x) (?:a|per) week (?:in|at|from) (?:the |our )?(?:office|site|clinic|hospital|school|store)|"
            r"fully remote|remote[- ]first|remote within [A-Z][\w ]{2,20}|in[- ]person \w+ days? (?:a|per) week|"
            r"(?:night|day|rotating|rotational) shifts?|weekend work(?:ing)?)\b",
            re.I,
        ),
    ),
    (
        "Vetting",
        re.compile(
            r"\b((?:enhanced |basic |standard )?DBS(?: check| clearance)?|(?:SC|DV|NPPV\d?|BPSS) (?:clearance|vetting)|"
            r"security clearance|disclosure scotland|PVG(?: scheme)?|background check)\b",
            re.I,
        ),
    ),
]


def _clean(line: str) -> str:
    return " ".join(line.replace(" ", " ").split())


def _is_heading(line: str, nxt: str | None) -> bool:
    """A short line that introduces what follows. Never a sentence: no full stop, not long."""
    text = line.strip()
    if not text or _BULLET.match(text) or len(text) > 70 or nxt is None:
        return False
    words = text.rstrip(":").split()
    if len(words) > 9:
        return False
    if text.endswith(":"):
        return True
    if re.search(r"[.!,;]$", text) or ". " in text:  # "Why join us?" is a heading
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 3 and all(c.isupper() for c in letters):
        return True
    # Title case or a heading word, and something to introduce. Or in sentence case, as UK public
    # sector posts write them ("Main duties of the job"), when it names a kind of section.
    capitals = sum(1 for w in words if w[:1].isupper() or not w[:1].isalpha())
    if _HEADING_WORDS.search(text) and (capitals >= len(words) * 0.5 or len(words) <= 4):
        return True
    return len(words) <= 7 and text[:1].isupper() and any(pattern.search(text.replace("’", "'")) for _, pattern in _KINDS)


_SMALL_WORDS = {
    "the",
    "and",
    "for",
    "you",
    "our",
    "who",
    "why",
    "how",
    "are",
    "all",
    "new",
    "its",
    "one",
    "out",
    "use",
    "get",
    "can",
    "own",
    "to",
    "of",
    "in",
    "at",
    "on",
    "we",
    "us",
    "is",
    "a",
    "an",
    "or",
    "be",
    "do",
    "by",
    "as",
    "it",
    "if",
    "up",
    "my",
    "me",
}


def _heading_text(line: str, company: str = "") -> str:
    text = _clean(line).rstrip(":").strip()
    letters = [c for c in text if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 3:
        # "WHERE YOU'LL CREATE IMPACT" → "Where you'll create impact": shouting isn't emphasis.
        # A short word that isn't English ("GTM", "ABM") is an acronym and stays as written.
        words = [w if len(w.strip("&/'’-")) <= 3 and w.lower() not in _SMALL_WORDS else w.lower() for w in text.split()]
        text = " ".join(words)
        text = text[:1].upper() + text[1:]
        if company:
            text = re.sub(re.escape(company), company, text, flags=re.I)
    return text


def _kind(heading: str | None, company: str) -> str:
    if heading is None:
        return "intro"
    heading = heading.replace("’", "'")
    if company and re.search(rf"\b(?:about|meet|life at|working at|why)\s+{re.escape(company)}\b", heading, re.I):
        return "company"
    for kind, pattern in _KINDS:
        if pattern.search(heading):
            return kind
    # "What Makes Cloudflare Special?", "Life at the Trust": the employer talking about itself.
    if company and company.lower() in heading.lower():
        return "company"
    return "other"


def _facts_from_label(line: str) -> JobFact | None:
    """ "Salary: £38,000", and "Job closing date: 06.12.2026" by its longest known part."""
    m = _FACT_LINE.match(line)
    if not m:
        return None
    label = _FACT_LABELS.get(m.group(1).strip().lower()) or _fact_heading(m.group(1).strip())
    return JobFact(label=label, value=_clean(m.group(2))) if label else None


_FACT_KEYS = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(_FACT_LABELS, key=len, reverse=True)) + r")\b")


def _fact_heading(heading: str | None) -> str | None:
    """The fact label a heading names, by its longest matching label: "Job closing date" → "Closing date"."""
    if not heading or len(heading.split()) > 4:
        return None
    m = _FACT_KEYS.search(heading.lower().replace("’", "'"))
    return _FACT_LABELS[m.group(1)] if m else None


# Visa terms are read as the sentence around them ("We are unable to offer visa sponsorship for
# this role"), so the anchor is found first and the sentence taken by hand: a pattern that starts
# with "up to 60 of anything" retries at every character of a 5,000-character post. "Sponsor"
# alone isn't enough — Cloudflare's posts mention "sponsorship for an export license".
_VISA = re.compile(r"sponsor\w*|right to work in|work (?:permit|authori[sz]ation)", re.I)
_VISA_SENSE = re.compile(r"visa|immigration|work (?:permit|authori[sz]ation)|right to work|skilled worker|h-?1b|green card", re.I)
_YEARS = re.compile(r"\d{1,2}\+?(?:\s*(?:-|–|to)\s*\d{1,2}\+?)?\s+years?\b", re.I)
_END = re.compile(r"[.!?\n;]")


def _sentence(text: str, start: int, end: int, limit: int = 160) -> str:
    left = max((m.end() for m in _END.finditer(text, max(0, start - limit), start)), default=max(0, start - limit))
    m = _END.search(text, end)
    right = m.end() if m else len(text)
    out = text[left:right].strip()
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0] + "…"
    return out


def _inferred(text: str, have: set[str]) -> list[JobFact]:
    out = []
    for label, pattern in _INFERRED:
        if label in have:
            continue
        if label == "Experience":
            # Only where a number of years is, so the expensive pattern runs on a few windows.
            m = next((pattern.match(text, max(0, y.start() - 20)) or pattern.search(text, max(0, y.start() - 20), y.end() + 60)
                      for y in _YEARS.finditer(text) if "experience" in text[y.end() : y.end() + 60].lower()), None)  # fmt: skip
        else:
            m = pattern.search(text)
        if m:
            value = _clean(m.group(1)).strip(" ,;-")
            out.append(JobFact(label=label, value=value[:1].upper() + value[1:]))
    if "Visa" not in have:
        for m in _VISA.finditer(text):
            sentence = _clean(_sentence(text, m.start(), m.end()))
            if _VISA_SENSE.search(sentence):
                out.append(JobFact(label="Visa", value=sentence[:1].upper() + sentence[1:]))
                break
    return out


def _tokens(text: str) -> list[tuple[str, str]]:
    """The text as (kind, text) lines: "h" a heading, "li"/"oli" a list item, "p" a paragraph, "" a gap."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    following: list[int | None] = [None] * len(lines)  # the next non-blank line after each
    after: int | None = None
    for i in range(len(lines) - 1, -1, -1):
        following[i] = after
        if lines[i].strip():
            after = i
    out: list[tuple[str, str]] = []
    skip: int | None = None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if i == skip:
            continue
        if not line:
            out.append(("", ""))
            continue
        j = following[i]
        nxt = lines[j] if j is not None else None
        if (
            nxt
            and len(line) <= 40  # a bare label, cheap to rule out first
            and ":" not in line.rstrip(":")  # not a label with its value already on the line
            and _fact_heading(line.rstrip(":"))
            and len(nxt.strip()) <= 60
            and not _BULLET.match(nxt.strip())
            and not _facts_from_label(nxt)
        ):
            # "Start date" on one line and "ASAP" on the next: one fact, written on two lines.
            out.append(("p", f"{line.rstrip(':')}: {nxt.strip()}"))
            skip = j
            continue
        md = _MD_HEADING.match(line)
        if md:
            out.append(("h", md.group(1)))
            continue
        if _BULLET.match(line):
            kind = "oli" if _ORDERED.match(line) else "li"
            out.append((kind, _BULLET.sub("", line, count=1)))
            continue
        glued = _GLUED.match(line)
        if glued and _is_heading(glued.group(1), nxt) and _HEADING_WORDS.search(glued.group(1)) and not _facts_from_label(line):
            # Only when what follows reads as a list item or a sentence, not "Salary: £30k".
            out.append(("h", glued.group(1)))
            out.append(("li" if line[len(glued.group(1)) :].lstrip().startswith(("-", "–")) else "p", glued.group(2)))
            continue
        if _is_heading(line, nxt):
            out.append(("h", line))
            continue
        if nxt and _BULLET.match(nxt.strip()) and len(line) <= 70 and not re.search(r"[.!?;,]$", line) and len(line.split()) <= 9:
            # A short line directly above a list names that list: a sub-heading in its section.
            out.append(("sh", line.rstrip(":")))
            continue
        out.append(("p", line))
    return out


def _blocks(lines: list[tuple[str, str]], kind: str) -> list[DescriptionBlock]:
    blocks: list[DescriptionBlock] = []
    for t, text in lines:
        text = _clean(text)
        if not t:
            continue
        if t in ("li", "oli"):
            want = "ol" if t == "oli" else "ul"
            if blocks and blocks[-1].t == want:
                blocks[-1].items = [*(blocks[-1].items or []), text]
            else:
                blocks.append(DescriptionBlock(t=want, items=[text]))
        elif t == "sh":
            blocks.append(DescriptionBlock(t="h", text=text))
        else:
            blocks.append(DescriptionBlock(t="p", text=text))
    # Lever and others write a list as one short paragraph per item. Under a heading that
    # introduces a list, three or more short paragraphs and nothing else are a list.
    paras = [b for b in blocks if b.t == "p"]
    listed = kind in ("role", "requirements", "nice", "benefits") and len(paras) >= 3 and len(paras) == len(blocks)
    if listed and all(len(b.text or "") <= 260 for b in paras):
        return [DescriptionBlock(t="ul", items=[b.text or "" for b in paras])]
    return blocks


_THE_JOB = re.compile(
    r"\byou(?:r|'ll|’ll|'re|’re)?\b|\bthis (?:role|position|job|post|vacancy|opportunity)\b|\bthe (?:role|position|post)\b|"
    r"\bwe(?:'re|’re| are) (?:seeking|looking|recruiting|hiring)\b|\bjoin (?:us|our)\b|successful (?:candidate|applicant)",
    re.I,
)


def _mentions_you(text: str, title: str) -> bool:
    """Where an opening stops being about the employer and reaches the job."""
    return bool(_THE_JOB.search(text)) or (bool(title) and title.lower() in text.lower())


def source_key(text: str, *, title: str = "", company: str = "") -> str:
    """What a view was laid out from: the text, and the title and company it reads it against."""
    return hashlib.sha1(f"{title}\x1f{company}\x1f{text}".encode()).hexdigest()[:16]


def describe(text: str | None, *, title: str = "", company: str = "") -> DescriptionView | None:
    """The view of one description, or None when there is nothing to lay out."""
    if not text or not text.strip():
        return None
    facts: list[JobFact] = []
    tokens = _tokens(text)

    # "Label: value" lines become facts, wherever they are; the labels are a fixed list, so a
    # sentence with a colon in it stays a sentence.
    kept: list[tuple[str, str]] = []
    for t, line in tokens:
        fact = _facts_from_label(line) if t in ("p", "h") else None
        if fact and fact.label not in {f.label for f in facts}:
            facts.append(fact)
        elif not fact:
            kept.append((t, line))

    # Sections at each heading.
    groups: list[tuple[str | None, list[tuple[str, str]]]] = [(None, [])]
    for t, line in kept:
        if t == "h" and groups[-1][0] is not None and not any(k for k, _ in groups[-1][1]):
            # A heading straight under another ("Person specification", then "Essential") is
            # a part of it, not a section of its own.
            groups[-1][1].append(("sh", _heading_text(line, company)))
        elif t == "h":
            groups.append((_heading_text(line, company), []))
        else:
            groups[-1][1].append((t, line))

    sections: list[DescriptionSection] = []
    small_print: list[DescriptionBlock] = []
    for heading, lines in groups:
        kind = _kind(heading, company)
        blocks = _blocks(lines, kind)
        label = _fact_heading(heading)
        if label and len(blocks) == 1 and blocks[0].t == "p" and len(blocks[0].text or "") <= 80:
            # CERN and others give a fact a heading of its own: "Contract duration" / "36 months".
            if label not in {f.label for f in facts}:
                facts.append(JobFact(label=label, value=blocks[0].text or ""))
            continue
        if kind != "legal":
            # Equal-opportunity text under some other heading (or none) is still the small print.
            fine = [b.t == "p" and bool(_SMALL_PRINT.search(b.text or "")) for b in blocks]
            small_print += [b for b, f in zip(blocks, fine, strict=True) if f]
            blocks = [b for b, f in zip(blocks, fine, strict=True) if not f]
        if not blocks:
            continue
        if kind == "intro":
            # An opening that is about the company until it reaches the job: the company part is
            # the blurb, folded; the rest is the job, shown.
            lead = 0
            while lead < len(blocks) and blocks[lead].t == "p" and not _mentions_you(blocks[lead].text or "", title):
                lead += 1
            if 0 < lead < len(blocks) and company and company.lower() in (blocks[0].text or "").lower():
                sections.append(DescriptionSection(heading=f"About {company}", kind="company", fold=True, blocks=blocks[:lead]))
                blocks = blocks[lead:]
        if kind == "legal":
            small_print += blocks
            continue
        sections.append(DescriptionSection(heading=heading, kind=kind, fold=kind in _FOLDED, blocks=blocks))  # type: ignore[arg-type]

    if small_print:
        sections.append(DescriptionSection(heading="The small print", kind="legal", fold=True, blocks=small_print))

    # From the whole post, small print included: "This post is subject to an Enhanced DBS check"
    # often shares a paragraph with the equal-opportunities line.
    have = {f.label for f in facts}
    facts += _inferred("\n".join(b.text or "\n".join(b.items or []) for s in sections for b in s.blocks), have)
    return DescriptionView(version=VERSION, of=source_key(text, title=title, company=company), facts=facts, sections=sections)
