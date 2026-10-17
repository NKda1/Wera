"""Contact redaction before any model call (PRIV-01), and the seam for a real PII gateway.

Phase 2 keeps this small on purpose. `ContactRedactor` masks the contact details WERA already
holds, which is all it can be sure about; it is not a PII detector. A later phase replaces it
with a detector (regex + spaCy + Presidio behind `LLM_PROXY_URL`) by implementing `Redactor` —
nothing else has to change, because `llm.gateway.structured()` is the only place prompts are
built and the only place this runs.

Contact details are also kept out of prompt inputs in the first place: `MasterCV` has no
contact fields, and they are read again only when a document is exported (EXP-02). This is the
second line, not the first.
"""

import re
from typing import Protocol

from .schemas.cv import Contact

EMAIL = "[[EMAIL]]"
PHONE = "[[PHONE]]"
ADDRESS = "[[ADDRESS]]"

type Mapping = dict[str, str]


class Redactor(Protocol):
    """`redact` returns the masked text and what it replaced; `restore` puts it back."""

    def redact(self, text: str) -> tuple[str, Mapping]: ...

    def restore(self, text: str, mapping: Mapping) -> str: ...


# Anything that could be a phone number: a run of digits with the usual separators. Matching
# candidates and then comparing digits is what makes a partial match impossible — a regex built
# from the number itself can start mid-way and leave "+44-" behind.
_CANDIDATE = re.compile(r"\+?\d[\d\s().-]{5,}\d")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


class ContactRedactor:
    """Masks the stored email, phone and address. Longest first, so an address that contains
    a shorter value is replaced whole."""

    def __init__(self, contact: Contact) -> None:
        self._literals: list[tuple[str, str]] = sorted(
            ((v, p) for v, p in ((contact.email, EMAIL), (contact.address, ADDRESS)) if v and v.strip()),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
        self._phone = contact.phone if contact.phone and len(_digits(contact.phone)) >= 7 else None
        # The last 9 digits identify the number whichever country prefix it is written with.
        self._tail = _digits(self._phone)[-9:] if self._phone else ""

    def _mask_phones(self, text: str) -> tuple[str, bool]:
        """Replaces whole phone-like runs whose digits match the stored number."""
        found = False

        def swap(match: re.Match[str]) -> str:
            nonlocal found
            if not _digits(match.group(0)).endswith(self._tail):
                return match.group(0)
            found = True
            return PHONE

        return (_CANDIDATE.sub(swap, text), found) if self._tail else (text, False)

    def redact(self, text: str) -> tuple[str, Mapping]:
        mapping: Mapping = {}
        for value, placeholder in self._literals:
            if value.lower() in text.lower():
                text = re.sub(re.escape(value), placeholder, text, flags=re.IGNORECASE)
                mapping[placeholder] = value
        text, found = self._mask_phones(text)
        if found and self._phone:
            mapping[PHONE] = self._phone
        return text, mapping

    def restore(self, text: str, mapping: Mapping) -> str:
        for placeholder, value in mapping.items():
            text = text.replace(placeholder, value)
        return text


class NullRedactor:
    """Used when a person turns redaction off in Settings, after the confirmation dialog."""

    def redact(self, text: str) -> tuple[str, Mapping]:
        return text, {}

    def restore(self, text: str, mapping: Mapping) -> str:
        return text
