"""A parsed CV, turned into a master CV with ids WERA assigns (CV-03, CV-04).

The ids are the point of this module. A tailored line is trustworthy because it cites a master
CV id (§8.5), so an id has to be unique and stable. A model is never asked for one: it reads
the CV and this assigns the ids, which means a duplicate cannot happen rather than being caught
later by `put_cv`.

Ids are readable on purpose — `exp-monzo-01` rather than a hash — because they show up in guard
violations and in the "which line did this come from" view, and a person has to be able to
follow them.

Dates get the same treatment. A CV writes them every way there is ("Sept 2024", "09/2024",
"2024-09", "Summer 2024"), and the stored contract wants `YYYY-MM`. Rejecting a whole CV over
one badly written date would be a poor trade for the person who just uploaded it, so what can
be read is repaired here and what can't becomes a blank the person fills in.
"""

import re

from ..schemas.cv import Bullet, Education, Language, Link, MasterCV, Person, Role, Summary
from ..schemas.llm_outputs import ParsedCv, ParsedRole
from .text import slug

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

PRESENT = re.compile(r"(?i)^\s*(present|current|now|ongoing|to date)\s*\.?\s*$")
ISO = re.compile(r"^(\d{4})-(\d{1,2})$")
SLASH = re.compile(r"^(\d{1,2})[/.](\d{4})$")  # 09/2024
NAMED = re.compile(r"(?i)^([a-z]+)\.?\s+(\d{4})$")  # Sept 2024
YEAR = re.compile(r"^(\d{4})$")


def year_month(raw: str | None, *, default_month: int = 1) -> str | None:
    """`YYYY-MM` from however the CV wrote it, or None when there is nothing to read.

    A bare year becomes January — or December for an end date, via `default_month`, so a role
    written as "2022 – 2024" spans the whole period rather than collapsing to a month.
    """
    value = (raw or "").strip()
    if not value or PRESENT.match(value):
        return None

    if m := ISO.match(value):
        year, month = int(m.group(1)), int(m.group(2))
    elif m := SLASH.match(value):
        month, year = int(m.group(1)), int(m.group(2))
    elif m := NAMED.match(value):
        month, year = MONTHS.get(m.group(1).lower()[:4].rstrip("."), 0), int(m.group(2))
        if not month:
            month = MONTHS.get(m.group(1).lower()[:3], 0)
        if not month:
            return None
    elif m := YEAR.match(value):
        year, month = int(m.group(1)), default_month
    else:
        return None

    if not (1 <= month <= 12) or not (1900 <= year <= 2100):
        return None
    return f"{year:04d}-{month:02d}"


def _end(raw: str | None) -> str:
    """An end date, or `present` — which is what an open-ended role means and what §6.1 stores."""
    if not (raw or "").strip() or PRESENT.match(raw or ""):
        return "present"
    return year_month(raw, default_month=12) or "present"


def _unique(base: str, taken: set[str]) -> str:
    """`base`, or `base-2`, `base-3`… so two roles at the same employer never collide."""
    if base not in taken:
        taken.add(base)
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    taken.add(f"{base}-{n}")
    return f"{base}-{n}"


def _role_id(prefix: str, org: str, index: int, taken: set[str]) -> str:
    """`exp-monzo`, falling back to position when the name slugs to nothing (CJK, symbols)."""
    name = slug(org)[:24].strip("-")
    return _unique(f"{prefix}-{name}" if name else f"{prefix}-{index + 1}", taken)


def _roles(parsed: list[ParsedRole], prefix: str, taken: set[str]) -> list[Role]:
    out: list[Role] = []
    for index, role in enumerate(parsed):
        role_id = _role_id(prefix, role.org, index, taken)
        out.append(
            Role(
                id=role_id,
                title=role.title.strip(),
                org=role.org.strip(),
                location=(role.location or "").strip() or None,
                # `start` is required by the contract; an unreadable one becomes a blank the
                # person fills in on the CV page, flagged there rather than guessed at here.
                start=year_month(role.start) or "",
                end=_end(role.end),
                bullets=[
                    Bullet(id=f"{role_id}-{n:02d}", text=text.strip(), tags=[])
                    for n, text in enumerate((b for b in role.bullets if b.strip()), start=1)
                ],
            )
        )
    return out


def to_master_cv(parsed: ParsedCv, *, version: int) -> MasterCV:
    """The stored contract, with every id assigned here.

    `version` is the *current* version: this is a proposal, and saving it is what increments
    (CV-03). Nothing here is written to the database.
    """
    taken: set[str] = set()
    experience = _roles(parsed.experience, "exp", taken)
    projects = _roles(parsed.projects, "prj", taken)

    education: list[Education] = []
    for index, entry in enumerate(parsed.education):
        education.append(
            Education(
                id=_role_id("edu", entry.institution, index, taken),
                degree=entry.degree.strip(),
                institution=entry.institution.strip(),
                start=year_month(entry.start),
                end=year_month(entry.end, default_month=12),
                notes=None,
            )
        )

    summary = (parsed.summary or "").strip()
    return MasterCV(
        version=version,
        person=Person(
            name=parsed.name.strip(),
            headline=(parsed.headline or "").strip() or None,
            location=parsed.location.strip(),
            links=[Link(label=x.label.strip(), url=x.url.strip()) for x in parsed.links if x.url.strip()],
        ),
        summary=Summary(id="sum-01", text=summary) if summary else None,
        experience=experience,
        projects=projects,
        education=education,
        skills=_skills(parsed.skills),
        languages=[Language(name=x.name.strip(), level=x.level.strip()) for x in parsed.languages if x.name.strip()],
    )


def _skills(names: list[str]) -> list:
    """De-duplicated, order kept. Levels are left unset — a CV rarely says, and a guessed
    "expert" is a claim the person never made."""
    from ..schemas.cv import Skill

    seen: set[str] = set()
    out = []
    for name in names:
        clean = name.strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            out.append(Skill(name=clean, aliases=None, level=None))
    return out
