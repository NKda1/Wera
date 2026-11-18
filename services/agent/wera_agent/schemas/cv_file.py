"""The CV file: the RenderCV document a pack's CV PDF is rendered from (EXP-01, 18 Nov)."""

from typing import Annotated, Literal

from pydantic import Field

from .base import Model, Opt

type PageSize = Literal["a4", "us-letter"]


class CvFileState(Model):
    """What the person chose for this pack's CV file. Absent until they choose something: the
    file is then WERA's, built from the tailored CV with the default design."""

    theme: str
    # They rewrote the `cv:` part themselves. Until then it is rebuilt from the tailored CV on
    # every read, so a line edited on the review screen is in the PDF too; from then on their
    # text is the CV, and line editing waits until they discard it.
    edited_by_user: bool = False


class CvBlock(Model):
    """One line of the CV as a reader sees it, in reading order. The Word file is written from
    these and the PDF lint checks against them (ATSC-06), so both follow the file."""

    t: Literal["heading", "row", "bullet", "body"]
    text: str
    right: Opt[str] = None  # a row's dates


class CvIssue(Model):
    """Why the file can't be rendered, where the person can find it."""

    message: str
    line: Opt[int] = None
    column: Opt[int] = None
    where: Opt[str] = None  # "cv.sections.Experience.0.start_date"


class CvTheme(Model):
    id: str
    label: str
    note: str
    design: str  # its `design:` block as YAML, for the editor to swap in


class CvFile(Model):
    """`GET /applications/{id}/cv-file`: the file, what is in it, and what can be done with it.
    No contact details: those are added when it is rendered."""

    yaml: str
    theme: str
    page_size: PageSize
    edited_by_user: bool
    editable: bool
    themes: list[CvTheme]
    name: str
    location: Opt[str] = None
    links: list[str]
    blocks: list[CvBlock]
    # Roles the tailored CV cites that the master CV no longer has; they are left out of the file.
    unresolved_role_ids: list[str]


class CvFileIn(Model):
    """Save or preview the file. `reset` discards the person's rewrite and keeps their design."""

    yaml: Opt[Annotated[str, Field(max_length=64_000)]] = None
    reset: bool = False


class CvPreview(Model):
    """The file rendered as page images, for the editor. Issues instead when it won't render."""

    pages: list[str]  # data:image/png;base64,…
    issues: list[CvIssue]
