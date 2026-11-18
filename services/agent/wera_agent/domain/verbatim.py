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


# ---- `supported`: a paraphrase held to its source ----
#
# A summary can't quote — "what you'll do" in four lines is a paraphrase by definition — so it is
# held to the post another way: every number it gives must be in the post as written, and most
# of its words must be. A summary item about on-call rotas for a post that never mentions them
# fails both; "Build the payments API in Go" for a post about the payments API in Go passes.

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORDS = re.compile(r"[a-z0-9][a-z0-9+#'’-]*")
_COMMON = frozenset(
    [
        "about",
        "after",
        "also",
        "and",
        "any",
        "are",
        "back",
        "been",
        "being",
        "both",
        "but",
        "can",
        "could",
        "each",
        "from",
        "have",
        "having",
        "into",
        "just",
        "like",
        "make",
        "many",
        "more",
        "most",
        "must",
        "need",
        "only",
        "other",
        "over",
        "same",
        "should",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "very",
        "well",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "within",
        "would",
        "your",
        "you'll",
        "you're",
        "role",
        "work",
        "working",
        "team",
        "position",
        "candidate",
        "company",
        "job",
    ]
)


def _root(word: str) -> str:
    """ "leading" and "lead", "managed" and "manage": the same word to a reader."""
    for ending in ("ings", "ing", "ed", "es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            word = word[: -len(ending)]
            break
    return word[:5]


def stems_of(text: str) -> set[str]:
    return {_root(w) for w in _WORDS.findall(_norm(text))}


def supported(claim: str | None, text: str | None, *, share: float = 0.6, stems: set[str] | None = None) -> bool:
    """Whether a paraphrase says only what `text` says: its numbers verbatim, and at least `share`
    of its content words (compared by root, so "managing" meets "manage")."""
    if not claim or not text:
        return False
    source = _norm(text)
    numbers = _NUMBER.findall(claim)
    if any(n not in source and n.replace(",", "") not in source.replace(",", "") for n in numbers):
        return False
    words = [w for w in _WORDS.findall(_norm(claim)) if len(w) >= 4 and w not in _COMMON]
    if not words:
        # "SQL", "C#": too short to weigh, so it has to be there whole.
        return _norm(claim).strip(" .,;:") in source
    have = stems if stems is not None else stems_of(text)
    return sum(_root(w) in have for w in words) / len(words) >= share


_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*")


def quoted_in(quote: str | None, text: str | None) -> bool:
    """`appears_in`, with an ellipsis read as a quoter reads it: words left out, not words written.
    "...for customers ranging from bloggers…" passes when each part is in the text, in order."""
    if not quote or not text:
        return False
    parts = [p for p in _ELLIPSIS.split(quote.strip()) if p.strip(" .,;:")]
    if not parts:
        return False
    if len(parts) == 1:
        return appears_in(parts[0], text)
    source, at = _norm(text), 0
    for part in parts:
        needle = _norm(part).strip(" .,;:")
        found = source.find(needle, at)
        if len(needle) < 3 or found == -1:
            return False
        at = found + len(needle)
    return True
