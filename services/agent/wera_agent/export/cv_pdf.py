"""Render the CV file to PDF, or to page images for the editor (EXP-01, EXP-02, EXP-03).

**The one place in the agent where contact details enter a document** (CV-06): the email address,
phone number and postal address are added to a copy of the file here, at render time, and the
stored file never holds them. Nothing in this module calls a model.

RenderCV's own pipeline is run step by step rather than through its CLI: validate, write the
Typst source into a temporary directory, compile. That is the same work `rendercv render` does,
without a subprocess per download, and it lets the compile take a **fixed timestamp** — RenderCV's
`generate_pdf` stamps the PDF with the current time, so the same pack would never render the
same bytes twice (EXP-03). With the timestamp and `current_date` both set to the approval time,
it does (checked across all nine themes, 18 Nov).
"""

import base64
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ..schemas.cv import Contact
from .cv_yaml import RENDERCV, CvFileInvalid, escape, validate

# Typst compilers are cached by RenderCV per root directory (`lru_cache(maxsize=1)`), and each
# render has its own temporary root; one render at a time keeps that cache coherent, and it is the
# same lock every RenderCV parse holds (`cv_yaml.RENDERCV`). A render takes about 120 ms once the
# fonts are loaded.
PREVIEW_PPI = 110


def _with_contact(document: dict[str, Any], contact: Contact) -> dict[str, Any]:
    """A copy of the file with the contact details in its header, in the order a reader expects:
    place, email, phone, then the links already in the file.

    The phone number is printed as typed, not through RenderCV's `phone` field: that field
    rejects any number its library doesn't recognise (Ofcom's reserved drama numbers, for one)
    and reformats the rest. An email address it doesn't accept is printed the same way.
    """
    from pydantic import EmailStr, TypeAdapter, ValidationError

    cv = dict(document["cv"])
    connections = list(cv.pop("custom_connections", None) or [])
    head: dict[str, Any] = {k: cv.pop(k) for k in ("name", "headline") if k in cv}
    place = contact.address or cv.pop("location", None)
    cv.pop("location", None)
    if place:
        head["location"] = escape(place) if contact.address else place
    first: list[dict[str, Any]] = []
    if contact.email:
        try:
            TypeAdapter(EmailStr).validate_python(contact.email)
            head["email"] = contact.email
        except ValidationError:
            first.append({"fontawesome_icon": "envelope", "placeholder": escape(contact.email), "url": None})
    if contact.phone:
        first.append({"fontawesome_icon": "phone", "placeholder": escape(contact.phone), "url": None})
    for key in ("website", "social_networks"):
        if key in cv:
            head[key] = cv.pop(key)
    if first or connections:
        head["custom_connections"] = first + connections
    return {**document, "cv": head | cv}


def _settings(work: Path, on: datetime) -> dict[str, Any]:
    return {
        "current_date": on.date().isoformat(),
        "render_command": {"output_folder": str(work), "typst_path": str(work / "cv.typ"), "dont_generate_png": True},
    }


def render(
    document: dict[str, Any], contact: Contact, *, on: datetime, fmt: Literal["pdf", "png"] = "pdf"
) -> bytes | list[bytes]:
    """The file as PDF bytes, or as one PNG per page. `on` is the date the CV is dated: the
    approval time once approved, so the bytes never change afterwards (EXP-03)."""
    from rendercv.renderer.pdf_png import get_typst_compiler
    from rendercv.renderer.typst import generate_typst

    with RENDERCV, tempfile.TemporaryDirectory(prefix="wera-cv-") as tmp:
        work = Path(tmp)
        full = _with_contact(document, contact) | {"settings": _settings(work, on)}
        model = validate(full, work=work)
        typst_path = generate_typst(model)
        if typst_path is None:  # pragma: no cover — only when dont_generate_typst is set
            raise CvFileInvalid([])
        compiler = get_typst_compiler(work / "cv.yaml", typst_path.parent)
        if fmt == "png":
            pages = compiler.compile(input=typst_path, format="png", ppi=PREVIEW_PPI)
            return pages if isinstance(pages, list) else [pages]
        return compiler.compile(input=typst_path, format="pdf", timestamp=int(on.timestamp()))


def preview(document: dict[str, Any], contact: Contact, *, on: datetime) -> list[str]:
    pages = render(document, contact, on=on, fmt="png")
    return [f"data:image/png;base64,{base64.b64encode(p).decode()}" for p in pages]


_PAGE = re.compile(rb"/Type\s*/Page(?![s\w])")


def page_count(pdf: bytes) -> int:
    """Pages in a PDF Typst wrote. Typst writes each page object uncompressed, so they can be
    counted without a PDF reader (checked against pypdf in the tests)."""
    return len(_PAGE.findall(pdf))
