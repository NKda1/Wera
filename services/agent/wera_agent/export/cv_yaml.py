"""The CV file as a RenderCV document (EXP-01, 18 Nov): built from a pack, checked, and read back.

RenderCV (MIT, rendercv.com) reads a YAML file in three parts — `cv:` (the content), `design:`
(a theme and its settings) and `locale:` — and typesets it with Typst. Each pack's CV is kept as
one of those files, so what the person edits in the app is a real RenderCV file and the PDF is
rendered from exactly that.

**No contact details here.** The file holds the name, headline, location and links from the
master CV; the email address and phone number are added by `cv_pdf.render` and nowhere else
(CV-06, EXP-02), and a file that carries its own is refused rather than stored.

**RenderCV reads every text field as Markdown, and passes `#name(…)` through as Typst code.** A
line from a CV is text, not markup, so `escape` makes it literal: "fees_owed" stays as written,
"#rust" doesn't call a Typst function, and "*" isn't emphasis. `plain` is the reverse — the
words a reader sees — and is what the Word file, the PDF lint and the ATS check work from.
"""

import json
import re
import threading
from functools import cache
from pathlib import Path
from typing import Any

from ..domain.places import country_of
from ..schemas.cv import Education, MasterCV, Role
from ..schemas.cv_file import CvBlock, CvIssue, CvTheme, PageSize
from ..schemas.jobs import Job
from ..schemas.pack import Cited, CvItem, CvSection, TailoredCV

# Every theme RenderCV 2.8 ships, in the order the picker shows them. The default reads in the
# order a parser should — a role's title, company and dates, then its bullets. `classic` and
# `opal` set the dates in a right-hand column that is extracted *after* the bullets (measured
# with pypdf, 18 Nov), which a parser can attach to the wrong role.
THEMES: dict[str, tuple[str, str]] = {
    "engineeringresumes": ("Engineering résumés", "Black, compact, one column. Reads in order: the default."),
    "sb2nov": ("sb2nov", "Title and company on two lines, dates on the right."),
    "engineeringclassic": ("Engineering classic", "Like the default, with a coloured name and rules."),
    "harvard": ("Harvard", "Serif, centred header, the US campus style."),
    "moderncv": ("ModernCV", "Dates in a left margin."),
    "ink": ("Ink", "Company first, title below."),
    "ember": ("Ember", "Warm accent colour, diamond bullets."),
    "classic": ("Classic", "RenderCV's own default. Dates are read after the bullets."),
    "opal": ("Opal", "Dates under each role's bullets, which some parsers misread."),
}
DEFAULT_THEME = "engineeringresumes"
LETTER_COUNTRIES = {"US", "CA"}

SECTION_TITLES = {"experience": "Experience", "projects": "Projects"}

# The header fields the file must not carry. Email and phone are contact details (above); a
# photo is left off because many parsers can't read past an image, and RenderCV would read the
# file it names from disk; `settings` is WERA's (paths, dates), not the person's.
CONTACT_KEYS = ("email", "phone")
REFUSED = {
    "email": "Your email address is added when the PDF is made, from your contact details. Remove it here.",
    "phone": "Your phone number is added when the PDF is made, from your contact details. Remove it here.",
    "photo": "Photos are left off: many ATS parsers can't read a CV with an image in it.",
    "settings": "WERA sets the render settings itself. Remove the settings block.",
}


def page_size(job: Job) -> PageSize:
    """US Letter for a job in the US or Canada, A4 everywhere else."""
    countries = country_of(job.location.country or job.location.raw)
    return "us-letter" if countries and countries <= LETTER_COUNTRIES else "a4"


@cache
def _justified() -> frozenset[str]:
    """The themes that justify their text, which Typst then hyphenates."""
    with RENDERCV:
        from rendercv.schema.models.design.built_in_design import discover_other_themes
        from rendercv.schema.models.design.classic_theme import ClassicTheme

    out = set()
    with RENDERCV:
        themes = [ClassicTheme, *discover_other_themes()]
    for theme in themes:
        field = theme.model_fields["typography"]
        typography = field.default_factory() if field.default_factory else field.default  # type: ignore[call-arg]
        if typography.alignment == "justified":
            out.add(theme.model_fields["theme"].default)
    return frozenset(out)


def default_design(theme: str = DEFAULT_THEME, size: PageSize = "a4") -> dict[str, Any]:
    """The theme with three things turned off.

    - **Hyphenation.** Seven of the nine themes justify their text, and Typst hyphenates
      justified text: "Kuber-netes" across a line end is two words to a parser, and neither is
      the keyword. RenderCV's `justified-with-no-hyphenation` keeps the look without the split.
    - **Icons**, which are Font Awesome glyphs a parser reads as stray characters.
    - **The top note**, "Last updated in Nov 2026", which says when the file was made.
    """
    design: dict[str, Any] = {"theme": theme, "page": {"size": size, "show_top_note": False}}
    if theme in _justified():
        design["typography"] = {"alignment": "justified-with-no-hyphenation"}
    design["header"] = {"connections": {"show_icons": False}}
    return design


def themes(size: PageSize) -> list[CvTheme]:
    """The picker's list. Each carries its `design:` block, which replaces the file's own when
    the person picks it: a theme is a design, so their other design changes go with the old one."""
    return [
        CvTheme(id=t, label=label, note=note, design=dump_design(default_design(t, size))) for t, (label, note) in THEMES.items()
    ]


def dump_design(design: dict[str, Any]) -> str:
    import yaml

    return yaml.dump({"design": design}, Dumper=_dumper(), sort_keys=False, allow_unicode=True, width=10_000)


DEFAULT_LOCALE = {"language": "english"}

# RenderCV reads YAML through one module-level ruamel parser (`schema.yaml_reader.yaml`), which
# isn't thread-safe, and the agent serves requests on a thread pool. In the e2e run on 18 Nov a
# line edit and the review rail's format check read at the same moment and both failed (IndexError,
# ParserError). Everything that reaches RenderCV holds this lock: parsing, validation, theme
# discovery and rendering (`cv_pdf`). Reentrant, because rendering validates.
RENDERCV = threading.RLock()

# ---------------------------------------------------------------------------
# Text in and out of RenderCV's Markdown

_ZWSP = "​"
_MD_SYNTAX = re.compile(r"([\\*\[\]])")
# `_` is emphasis only at a word's edge ("_this_"), never inside one ("fees_owed").
_EDGE_UNDERSCORE = re.compile(r"(?<!\w)_|_(?!\w)")
# What RenderCV hands to Typst unescaped: a `#name` call, `$$…$$` maths and an HTML-looking tag.
# A zero-width space after the first character breaks each without changing what is printed.
_TYPST_CODE = re.compile(r"([#$<])(?=[A-Za-z/$])")


def escape(text: str) -> str:
    """A line of CV text, so RenderCV prints it exactly as written."""
    text = " ".join(text.split()).replace("`", "'")  # a backtick opens raw Typst, escaped or not
    text = _MD_SYNTAX.sub(r"\\\1", text)
    text = _EDGE_UNDERSCORE.sub(r"\\_", text)
    text = _TYPST_CODE.sub(lambda m: m.group(1) + _ZWSP, text)
    return "\\" + text if text.startswith("!!!") else text  # an admonition block otherwise


@cache
def _markdown() -> Any:
    """Python-Markdown configured as RenderCV configures it, so `plain` reads what it prints."""
    import markdown

    md = markdown.Markdown()
    for processor in ("hashheader", "setextheader", "olist", "ulist", "quote"):
        md.parser.blockprocessors.deregister(processor)
    return md


_TAG = re.compile(r"<[^>]+>")


def plain(text: Any) -> str:
    """The words a reader sees in one RenderCV field: no Markdown, no escapes."""
    import html

    if not isinstance(text, str):
        return "" if text is None else str(text)
    md = _markdown()
    out = html.unescape(_TAG.sub("", md.reset().convert(text)))
    return " ".join(out.replace(_ZWSP, "").split())


# ---------------------------------------------------------------------------
# Building the file from a pack


def _dates(start: str | None, end: str | None) -> dict[str, str]:
    """RenderCV wants a start before an end; a role with no readable start gets neither."""
    if not start:
        return {}
    return {"start_date": start, **({"end_date": end} if end else {})}


def _role(role: Role, bullets: list[str]) -> dict[str, Any]:
    entry: dict[str, Any] = {"company": escape(role.org), "position": escape(role.title)}
    entry |= _dates(role.start, role.end)
    if role.location:
        entry["location"] = escape(role.location)
    if bullets:
        entry["highlights"] = [escape(b) for b in bullets]
    return entry


_DEGREE = re.compile(
    r"^((?:B|M|D)(?:A|Sc|S|Eng|Ed|Phil|Res|BA|Arch|Mus|Des|Pharm|Tech|Com|N)|PhD|DPhil|LLB|LLM|MBA|MD|HND|HNC|PGDip|PGCert)"
    r"\.?(\s*\(Hons\))?\s+(?:in\s+|of\s+)?(.+)$",
    re.I,
)


def split_degree(degree: str) -> tuple[str | None, str]:
    """`BSc (Hons) Computer Science` → (`BSc (Hons)`, `Computer Science`).

    RenderCV keeps the qualification and the subject apart, and some themes print "DEGREE in
    AREA". A degree it can't split stays whole as the subject, with no qualification.
    """
    m = _DEGREE.match(degree.strip())
    if not m:
        return None, degree.strip()
    return (m.group(1) + (m.group(2) or "")).strip(), m.group(3).strip()


def _education(ed: Education) -> dict[str, Any]:
    qualification, subject = split_degree(ed.degree)
    entry: dict[str, Any] = {"institution": escape(ed.institution), "area": escape(subject)}
    if qualification:
        entry["degree"] = escape(qualification)
    entry |= _dates(ed.start, ed.end) if ed.start else ({"date": ed.end[:4]} if ed.end else {})
    if ed.notes:
        entry["highlights"] = [escape(n.text) for n in ed.notes]
    return entry


def _link(url: str) -> dict[str, Any]:
    """A link as RenderCV's plain "custom connection": the address as text, linked when it's a
    web address RenderCV accepts. Not `social_networks`, which wants a username per network."""
    from pydantic import HttpUrl, TypeAdapter, ValidationError

    shown = re.sub(r"^https?://", "", url.strip()).rstrip("/")
    href: str | None = url.strip() if re.match(r"^https?://", url.strip()) else f"https://{shown}"
    try:
        TypeAdapter(HttpUrl).validate_python(href)
    except ValidationError:
        href = None
    return {"fontawesome_icon": "link", "placeholder": escape(shown), "url": href}


def build(
    doc: TailoredCV, cv: MasterCV, *, design: dict[str, Any] | None = None, locale: dict[str, Any] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """The file for one pack, and the roles it had to leave out.

    TAIL-02 as before: role headers, dates and education come from the master CV, never from the
    pack. A role the master CV no longer has can't get a header, so it is left out and reported.
    """
    roles = {r.id: r for r in [*cv.experience, *cv.projects]}
    sections: dict[str, list[Any]] = {}
    unresolved: list[str] = []

    if doc.summary.text.strip():
        sections["Summary"] = [escape(doc.summary.text)]
    for section in doc.sections:
        entries = []
        for item in section.items:
            role = roles.get(item.role_id)
            if role is None:
                unresolved.append(item.role_id)
                continue
            entries.append(_role(role, [b.text for b in item.bullets]))
        if entries:
            sections[SECTION_TITLES[section.kind]] = entries
    if cv.education:
        sections["Education"] = [_education(e) for e in cv.education]
    if doc.skills:
        sections["Skills"] = [escape(" · ".join(doc.skills))]

    person: dict[str, Any] = {"name": escape(cv.person.name)}
    if cv.person.headline:
        person["headline"] = escape(cv.person.headline)
    if cv.person.location:
        person["location"] = escape(cv.person.location)
    if cv.person.links:
        person["custom_connections"] = [_link(link.url) for link in cv.person.links]
    person["sections"] = sections
    return {"cv": person, "design": design or default_design(), "locale": locale or dict(DEFAULT_LOCALE)}, unresolved


@cache
def _dumper() -> Any:
    import yaml

    class Dumper(yaml.SafeDumper):
        """Lists indented under their key, as RenderCV's own sample files are written."""

        def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
            super().increase_indent(flow, False)

    return Dumper


def dump(document: dict[str, Any]) -> str:
    """The file as the editor shows it: one line per bullet, keys in RenderCV's order."""
    import yaml

    head = "# A RenderCV file (rendercv.com). Your email and phone are added when the PDF is made.\n"
    return head + yaml.dump(document, Dumper=_dumper(), sort_keys=False, allow_unicode=True, width=10_000)


def content(document: dict[str, Any]) -> str:
    """The `cv:` part as a comparable string: what decides whether the person rewrote it."""
    return json.dumps(document.get("cv"), sort_keys=True, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Reading a file the person wrote


class CvFileInvalid(Exception):
    def __init__(self, issues: list[CvIssue]):
        super().__init__(issues[0].message if issues else "The CV file isn't valid.")
        self.issues = issues


def _at(node: Any, key: str) -> tuple[int | None, int | None]:
    """Where `key` is written in a ruamel map, 1-based."""
    try:
        line, col = node.lc.key(key)
        return line + 1, col + 1
    except (AttributeError, KeyError, TypeError):
        return None, None


def _issues(err: Any) -> list[CvIssue]:
    """RenderCV's errors, most specific only. It heads a section's errors with one of its own
    ("There are problems with the entries… shown below"), which says nothing on its own line."""
    out = []
    for e in getattr(err, "validation_errors", []):
        (line, column) = e.yaml_location[0] if e.yaml_location else (None, None)
        where = ".".join(str(p) for p in e.schema_location) if e.schema_location else None
        out.append(CvIssue(message=e.message, line=line, column=column, where=where))
    out = [i for i in out if not (i.where and any(o.where and o.where.startswith(f"{i.where}.") for o in out))]
    return out or [CvIssue(message=str(getattr(err, "message", None) or err))]


def _jsonable(value: Any) -> str:
    """A YAML date (`2024-09-01`) as the string RenderCV reads; ruamel parses it as a datetime."""
    return value.isoformat()[:10] if hasattr(value, "isoformat") else str(value)


def _tree(text: str) -> Any:
    from rendercv.exception import RenderCVUserError, RenderCVUserValidationError
    from rendercv.schema.rendercv_model_builder import read_yaml_with_validation_errors

    try:
        with RENDERCV:
            tree = read_yaml_with_validation_errors(text, "main_yaml_file")
    except RenderCVUserValidationError as err:
        raise CvFileInvalid(_issues(err)) from err
    except RenderCVUserError as err:
        raise CvFileInvalid([CvIssue(message=err.message or "The file is empty.")]) from err
    except Exception as err:  # ruamel raises more than YAMLError on some malformed input
        raise CvFileInvalid([CvIssue(message=f"This is not a valid YAML file ({type(err).__name__}).")]) from err
    if not isinstance(tree, dict) or not isinstance(tree.get("cv"), dict):
        raise CvFileInvalid([CvIssue(message="The file needs a cv: section, as RenderCV's do.", line=1)])
    return tree


def parse(text: str) -> dict[str, Any]:
    """A stored file as plain data, without checking it again: it was checked when it was saved,
    and `cv_pdf.render` validates whatever it renders."""
    return json.loads(json.dumps(_tree(text), default=_jsonable))


def load(text: str) -> dict[str, Any]:
    """The person's file, checked the way RenderCV checks it plus WERA's own rules, as plain data.

    Raises `CvFileInvalid` with every problem RenderCV found, each with its line where it has
    one, so the editor can point at it.
    """
    tree = _tree(text)
    issues: list[CvIssue] = []
    for key in tree:
        if key not in ("cv", "design", "locale"):
            line, col = _at(tree, key)
            issues.append(
                CvIssue(
                    message=REFUSED.get(key, f"RenderCV doesn't read {key}:, and neither does WERA."),
                    line=line,
                    column=col,
                    where=key,
                )
            )
    for key in (*CONTACT_KEYS, "photo"):
        if tree["cv"].get(key) is not None:
            line, col = _at(tree["cv"], key)
            issues.append(CvIssue(message=REFUSED[key], line=line, column=col, where=f"cv.{key}"))
    if issues:
        raise CvFileInvalid(issues)

    data = json.loads(json.dumps(tree, default=_jsonable))
    validate(data, tree)
    return data


def validate(document: dict[str, Any], tree: Any = None, *, work: Path | None = None) -> Any:
    """RenderCV's own validation. Returns the RenderCV model.

    `work` is the directory a render writes into; RenderCV resolves every path in the file
    against the input file's, so that is where the input is said to be. A document that brings
    its own `settings` (only `cv_pdf.render`'s do) keeps them; any other is checked with
    settings that write nowhere.
    """
    import tempfile

    from rendercv.exception import RenderCVUserValidationError
    from rendercv.schema.rendercv_model_builder import build_rendercv_model_from_commented_map

    # RenderCV maps its errors to lines through the ruamel tree, so validate that when there is
    # one; the settings WERA adds aren't in it and can't be what fails.
    target = tree if tree is not None else document
    had = "settings" in target
    base = work or Path(tempfile.gettempdir())
    if not had:
        target["settings"] = {"current_date": "today", "render_command": {"output_folder": str(base)}}
    try:
        with RENDERCV:
            return build_rendercv_model_from_commented_map(target, input_file_path=base / "cv.yaml")
    except RenderCVUserValidationError as err:
        raise CvFileInvalid(_issues(err)) from err
    finally:
        if not had:
            del target["settings"]


# ---------------------------------------------------------------------------
# Reading the content back out: blocks for the Word file and the lint, a CV for the ATS check


def _year(value: Any) -> str:
    return str(value)[:4] if value else ""


def _span(entry: dict[str, Any]) -> str:
    """`export/content.ts`'s `roleDates`: years only, one year printed once."""
    if entry.get("date"):
        return plain(entry["date"])
    start, end = _year(entry.get("start_date")), entry.get("end_date")
    end = "present" if end == "present" else _year(end)
    if not start:
        return end
    return end if not end or start == end else f"{start} – {end}"


def _entry_blocks(entry: Any) -> list[CvBlock]:
    if isinstance(entry, str):
        return [CvBlock(t="body", text=plain(entry))]
    if not isinstance(entry, dict):
        return []
    out: list[CvBlock] = []
    where = f", {plain(entry['location'])}" if entry.get("location") else ""
    if "company" in entry:
        out.append(CvBlock(t="row", text=f"{plain(entry['position'])} — {plain(entry['company'])}{where}", right=_span(entry)))
    elif "institution" in entry:
        degree = " ".join(p for p in (plain(entry.get("degree")), plain(entry.get("area"))) if p)
        right = _year(entry.get("end_date")) or _span(entry)
        out.append(CvBlock(t="row", text=f"{degree} — {plain(entry['institution'])}", right=right))
    elif "name" in entry:
        out.append(CvBlock(t="row", text=f"{plain(entry['name'])}{where}", right=_span(entry)))
    elif "label" in entry:
        return [CvBlock(t="body", text=f"{plain(entry['label'])}: {plain(entry.get('details'))}")]
    elif "bullet" in entry:
        return [CvBlock(t="bullet", text=plain(entry["bullet"]))]
    elif "title" in entry:
        parts = [plain(entry["title"]), ", ".join(plain(a) for a in entry.get("authors") or []), plain(entry.get("journal"))]
        return [CvBlock(t="body", text=". ".join(p for p in parts if p))]
    else:
        text = plain(entry.get("number") or entry.get("reversed_number"))
        return [CvBlock(t="body", text=text)] if text else []
    if entry.get("summary"):
        out.append(CvBlock(t="body", text=plain(entry["summary"])))
    out += [CvBlock(t="bullet", text=plain(h)) for h in entry.get("highlights") or []]
    return out


def _title(key: str) -> str:
    """RenderCV prints a snake_case key as words: `selected_honors` → "Selected Honors"."""
    return key.replace("_", " ").title() if key == key.lower() else key


def blocks(document: dict[str, Any]) -> list[CvBlock]:
    out: list[CvBlock] = []
    for key, entries in (document["cv"].get("sections") or {}).items():
        out.append(CvBlock(t="heading", text=_title(key)))
        for entry in entries or []:
            out += _entry_blocks(entry)
    return out


def header(document: dict[str, Any]) -> tuple[str, str | None, list[str]]:
    """The name, the location and the links, as printed. Contact details are added at render."""
    cv = document["cv"]
    links = [plain(c.get("placeholder")) for c in cv.get("custom_connections") or []]
    sites = cv.get("website") or []
    links += [re.sub(r"^https?://", "", str(s)).rstrip("/") for s in (sites if isinstance(sites, list) else [sites])]
    links += [plain(n.get("username")) for n in cv.get("social_networks") or []]
    return plain(cv.get("name")), plain(cv.get("location")) or None, [link for link in links if link]


def text(document: dict[str, Any]) -> str:
    """Everything the file prints apart from contact details, one line per block."""
    cv = document["cv"]
    head = [plain(cv.get("name")), plain(cv.get("headline")), plain(cv.get("location"))]
    return "\n".join([*[h for h in head if h], *[b.text for b in blocks(document)]])


_SUMMARY = re.compile(r"summary|profile|about|statement|objective", re.I)
_SKILLS = re.compile(r"skill", re.I)
_EDUCATION = re.compile(r"education|qualification", re.I)
_SKILL_SPLIT = re.compile(r"\s*(?:·|,|;|\|)\s*")


def _fold(s: str) -> str:
    return " ".join(s.casefold().split())


def as_tailored(document: dict[str, Any], doc: TailoredCV, cv: MasterCV) -> TailoredCV:
    """The person's file read as a tailored CV, so the ATS report measures what they'll send.

    The pack's own tailored CV isn't changed: it is WERA's draft with its citations, and comes
    back if they discard their edits. Each entry is matched to its master-CV role by title and
    organisation, then by position within its section, so "shown in your latest role" still
    knows which role is the latest.
    """
    roles = [*cv.experience, *cv.projects]
    by_name = {(_fold(r.title), _fold(r.org)): r.id for r in roles}
    drafted = {s.kind: [i.role_id for i in s.items] for s in doc.sections}
    cited = {b.text: b.source_ids for s in doc.sections for i in s.items for b in i.bullets}

    summary: list[str] = []
    skills: list[str] = []
    sections: dict[str, list[CvItem]] = {"experience": [], "projects": []}
    synthetic = 0
    for key, entries in (document["cv"].get("sections") or {}).items():
        if _SUMMARY.search(key):
            summary += [b.text for e in entries or [] for b in _entry_blocks(e) if b.t != "heading"]
        elif _SKILLS.search(key):
            for e in entries or []:
                line = plain(e) if isinstance(e, str) else plain(e.get("details")) if isinstance(e, dict) else ""
                skills += [s for s in _SKILL_SPLIT.split(line) if s]
        elif not _EDUCATION.search(key):
            kind = "projects" if "project" in key.casefold() else "experience"
            for e in entries or []:
                if not isinstance(e, dict) or not (e.get("highlights") or "company" in e or "name" in e):
                    continue
                title, org = plain(e.get("position") or e.get("name")), plain(e.get("company"))
                role_id = by_name.get((_fold(title), _fold(org)))
                if role_id is None:
                    nth = len(sections[kind])
                    role_id = drafted.get(kind, [])[nth] if nth < len(drafted.get(kind, [])) else None
                if role_id is None:
                    synthetic += 1
                    role_id = f"cv-file-{synthetic}"
                bullets = [plain(h) for h in e.get("highlights") or []]
                sections[kind].append(
                    CvItem(
                        role_id=role_id,
                        bullets=[Cited(text=b, source_ids=cited.get(b, []), edited_by_user=b not in cited) for b in bullets],
                    )
                )
    return doc.model_copy(
        update={
            "summary": Cited(text=" ".join(summary), source_ids=doc.summary.source_ids, edited_by_user=True),
            "sections": [CvSection(kind=k, items=items) for k, items in sections.items() if items],  # type: ignore[arg-type]
            "skills": skills,
        }
    )
