"""Is this span really in that text? The check that lets a model quote without inventing.

A model asked for "the problem this team is solving" will write one whether or not the post
states it. Asking for a quote instead, and checking the quote, turns "don't invent" from an
instruction into a property: an excerpt that isn't in the source is dropped in code.

Lenient only about what a model can't see or reproduce faithfully — markdown emphasis, line
wrapping, curly quotes and case — and strict about the words themselves.
"""

import re

_EMPHASIS = re.compile(r"[*_`]+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def _norm(text: str) -> str:
    return " ".join(_EMPHASIS.sub("", text.translate(_QUOTES)).split()).casefold()


def appears_in(span: str | None, text: str | None) -> bool:
    """Whether `span` is a non-trivial excerpt of `text`, word for word."""
    if not span or not text:
        return False
    needle = _norm(span).strip(" .,;:")
    return len(needle) >= 3 and needle in _norm(text)
