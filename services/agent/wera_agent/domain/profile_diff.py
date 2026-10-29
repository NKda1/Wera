"""What a regenerated profile would change (PRF-04).

The point is that nothing is overwritten silently: the person sees, per field, what would be
added and what would be dropped, and chooses field by field. So the diff is computed here and
the decision is theirs — `POST /profile/generate` never saves.
"""

from typing import Any

from ..schemas.profile import SearchProfile

# Fields the model proposes. `sites`, `countries` and `channels` are the person's own settings —
# carried through unchanged by `generate_profile`, so there is nothing to diff — and
# `generatedFromCvVersion`/`updatedAt` are bookkeeping.
#
# Anything carried through MUST be carried explicitly in `generate_profile`. A field that is
# neither proposed nor carried is silently reset to its default and the diff cannot show it,
# because a diff of two equal-by-default values is empty. That is how `countries` was lost.
FIELDS = ("titles", "skills", "locations", "seniority", "exclude")
SCALARS = ("remote", "recency")


def _norm(value: str) -> str:
    return value.strip().casefold()


def diff_lists(current: list[str], proposed: list[str]) -> dict[str, list[str]]:
    """Added, removed and kept, in the order each side lists them."""
    have = {_norm(v) for v in current}
    want = {_norm(v) for v in proposed}
    return {
        "added": [v for v in proposed if _norm(v) not in have],
        "removed": [v for v in current if _norm(v) not in want],
        "kept": [v for v in current if _norm(v) in want],
    }


def profile_diff(current: SearchProfile, proposed: SearchProfile) -> dict[str, Any]:
    """`{field: {added, removed, kept}}` for list fields, `{field: {from, to}}` for the rest.

    Only fields that actually differ appear, so an empty dict means "nothing to review".
    """
    out: dict[str, Any] = {}
    for field in FIELDS:
        change = diff_lists(getattr(current, field), getattr(proposed, field))
        if change["added"] or change["removed"]:
            out[field] = change
    for field in SCALARS:
        before, after = getattr(current, field), getattr(proposed, field)
        if before != after:
            out[field] = {"from": before, "to": after}
    return out
