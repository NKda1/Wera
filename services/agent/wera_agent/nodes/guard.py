"""The citation guard's repair pass (SPEC §8.5 step 6).

`domain.guard.check_cited` says which written lines aren't carried by the master-CV entries they
cite. This adds what happens next: one `guard.repair` call per failing line, and if the line
still doesn't hold, it is dropped and the drop is shown to the person rather than quietly kept.

Dropping is the right outcome, not a failure: WERA would rather send a shorter CV than one that
says something the applicant can't back up (ARCHITECTURE §1, honesty).
"""

from sqlalchemy.orm import Session

from ..domain.guard import check_cited
from ..domain.vocab import cv_line_index
from ..errors import ApiError
from ..llm.gateway import structured
from ..schemas.cv import MasterCV
from ..schemas.llm_outputs import RepairOutput
from ..schemas.pack import Cited, GuardResult, Violation

# location → the line to use instead, or None to drop it.
type Decisions = dict[str, Cited | None]


def _sources_text(cv: MasterCV, source_ids: list[str]) -> str:
    lines = cv_line_index(cv)
    return "\n".join(f"[{i}] {lines[i]['text']}" for i in source_ids if i in lines) or "(none of the cited ids exist)"


def guard_and_repair(
    session: Session,
    items: list[tuple[str, Cited]],
    cv: MasterCV,
    keywords: list[str],
) -> tuple[GuardResult, Decisions]:
    """Checks every line, repairs each failing one once, and reports what is left.

    Returns the guard result to store with the document and, per location, the replacement line
    or `None` for "drop it". A line the person edited themselves is never touched (G2-02).
    """
    first = check_cited(items, cv, keywords)
    if first.pass_:
        return first, {}

    by_location = dict(items)
    failing = {v.location for v in first.violations}
    decisions: Decisions = {}

    for location in sorted(failing):
        item = by_location.get(location)
        if item is None:
            continue
        reasons = "\n".join(f"- {v.rule}: {v.detail}" for v in first.violations if v.location == location)
        try:
            out = structured(
                session,
                "guard.repair",
                RepairOutput,
                {"text": item.text, "sources": _sources_text(cv, item.source_ids), "violations": reasons},
                context={"cv": cv},
            )
        except ApiError:
            # A repair that can't run is the same as a repair that failed: drop the line.
            decisions[location] = None
            continue
        if out.item is None:
            decisions[location] = None
            continue
        decisions[location] = Cited(text=out.item.text, source_ids=out.item.source_ids)

    # Re-check what the repairs produced; anything still failing is dropped.
    repaired = [(loc, decisions.get(loc) or by_location[loc]) for loc in sorted(failing) if decisions.get(loc) is not None]
    second = check_cited(repaired, cv, keywords) if repaired else GuardResult(pass_=True, violations=[], repaired=False)
    for violation in second.violations:
        decisions[violation.location] = None

    dropped = sorted(loc for loc, value in decisions.items() if value is None)
    violations = [Violation(location=loc, rule="dropped", detail=_dropped_detail(loc, first)) for loc in dropped]
    return GuardResult(pass_=not violations, violations=violations, repaired=bool(failing)), decisions


WHY = {
    "numbers": "a number that isn't in the lines it cited",
    "terms": "a skill that isn't in the cited lines or your CV",
    "sources": "no usable master-CV source",
}


def _dropped_detail(location: str, first: GuardResult) -> str:
    """What the person is told about a line WERA left out, using that line's own first failure."""
    rules = {v.rule for v in first.violations if v.location == location}
    named = next((WHY[rule] for rule in ("numbers", "terms", "sources") if rule in rules), "a claim your CV doesn't carry")
    return f"Left out: it had {named}, and rewriting it didn't fix that."
