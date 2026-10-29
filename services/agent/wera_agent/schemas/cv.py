"""Master CV and contact details (SPEC §6.1)."""

import re
from typing import Annotated, Literal

from pydantic import WithJsonSchema

from .base import Model, Opt, check, required

_YM = re.compile(r"^\d{4}-\d{2}$")

type YearMonth = Annotated[
    str,
    check(lambda v: bool(_YM.match(v)), "year_month", "Use YYYY-MM"),
    WithJsonSchema({"type": "string", "pattern": _YM.pattern}),
]
type SkillLevel = Literal["familiar", "proficient", "expert"]
NonEmpty = Annotated[str, required("This can't be empty")]


class Bullet(Model):
    id: NonEmpty  # "exp-itu-01", stable forever
    text: str
    tags: list[str]


class Role(Model):
    id: NonEmpty
    title: Annotated[str, required("Add a title")]
    org: Annotated[str, required("Add an organisation")]
    location: Opt[str] = None
    # Empty is allowed here and refused on save (`Store.put_cv`). A CV writes dates every way
    # there is, and an uploaded one that couldn't be read for a single "Summer 2024" would fail
    # the whole import — so a date WERA can't read arrives blank for the person to fill in, and
    # cannot be saved that way. The same split as the rest of CV-03: a proposal is permissive,
    # what gets stored is not.
    start: YearMonth | Literal[""]
    end: YearMonth | Literal["present"]
    bullets: list[Bullet]


class Link(Model):
    label: str
    url: str


class Person(Model):
    name: Annotated[str, required("Add your name")]
    headline: Opt[str] = None
    location: str
    links: list[Link]


class Summary(Model):
    id: NonEmpty
    text: str


class Education(Model):
    id: NonEmpty
    degree: Annotated[str, required("Add a degree")]
    institution: Annotated[str, required("Add an institution")]
    start: Opt[YearMonth] = None
    end: Opt[YearMonth] = None
    notes: Opt[list[Bullet]] = None


class Skill(Model):
    name: NonEmpty
    aliases: Opt[list[str]] = None
    level: Opt[SkillLevel] = None


class Language(Model):
    name: str
    level: str


class MasterCV(Model):
    version: Annotated[int, check(lambda v: v >= 0, "version", "Version can't be negative")]  # increments on every save
    person: Person
    summary: Opt[Summary] = None
    experience: list[Role]
    projects: list[Role]  # same shape; org = project name
    education: list[Education]
    skills: list[Skill]
    languages: list[Language]


class Contact(Model):
    """CV-06: stored separately from the CV and never sent to a model."""

    email: str
    phone: Opt[str] = None
    address: Opt[str] = None


class CvLine(Model):
    text: str
    where: str
