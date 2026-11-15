"""What WERA learns from a person's Gate 1 decisions (MATCH-11…13), and the changes it proposes.

Computed in code from the `decisions` table — no model call — by `services.learning`.
"""

from typing import Literal

from .base import Model

# The reject dialog's presets, as codes. Free text is kept apart from these, in the note.
type ReasonCode = Literal["wrong_level", "wrong_location", "not_my_field", "pay_too_low", "applied_elsewhere"]

type SuggestionKind = Literal["exclude_term", "remove_title", "add_title", "add_location"]


class LearnedRank(Model):
    """MATCH-12: how far a job moved in the order, and why. Never part of `fit.total`."""

    adjustment: float  # added to the total for ordering only, within ±`learning.MAX_SHIFT`
    reason: str


class Suggestion(Model):
    """MATCH-13: a change to the search profile that the person's decisions point at. Nothing
    changes until they accept it (PRF-04)."""

    id: str  # `{kind}:{value}`, lowercased — what a dismissal remembers
    kind: SuggestionKind
    value: str  # the term, title or place, as it would be written into the profile
    message: str  # the whole suggestion, as the card reads it
    evidence: int  # decisions behind it


class SuggestionList(Model):
    items: list[Suggestion]
    decisions: int  # Gate 1 decisions learned from
