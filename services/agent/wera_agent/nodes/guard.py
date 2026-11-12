"""The citation guard's repair pass (SPEC §8.5 step 6).

`domain.guard.check_cited` says which written lines aren't carried by the master-CV entries they
cite. This adds what happens next: one `guard.repair` call per failing line, and if the line
still doesn't hold, it is dropped and the drop is shown to the person rather than quietly kept.

Dropping is the right outcome, not a failure: WERA would rather send a shorter CV than one that
says something the applicant can't back up (ARCHITECTURE §1, honesty).
"""

import logfire
from sqlalchemy.orm import Session

from ..domain.guard import check_cited
from ..domain.vocab import backing_text, cv_line_index
from ..errors import ApiError
from ..llm.gateway import structured
from ..schemas.cv import MasterCV
from ..schemas.llm_outputs import ClaimCheck, RepairOutput
from ..schemas.pack import Cited, GuardResult, Violation

# location → the line to use instead, or None to drop it.
type Decisions = dict[str, Cited | None]


def _sources_text(cv: MasterCV, source_ids: list[str]) -> str:
    lines = cv_line_index(cv)
    return "\n".join(f"[{i}] {lines[i]['text']}" for i in source_ids if i in lines) or "(none of the cited ids exist)"


def claim_violations(session: Session, items: list[tuple[str, Cited]], cv: MasterCV) -> list[Violation]:
    """One `guard.claims` call over every line: what each says about the applicant that its cited
    lines and the CV's facts don't.

    A failed call adds nothing rather than failing the document: the regex rules have already run,
    and dropping paragraphs because the checker was unreachable would punish the letter for an
    outage. It is logged, so a run of these shows up in Logfire.
    """
    checkable = [(loc, it) for loc, it in items if not it.edited_by_user and it.text.strip()]
    if not checkable:
        return []
    blocks = "\n\n".join(
        f"[{loc}] cites:\n{_sources_text(cv, it.source_ids) if it.source_ids else '(nothing)'}\nText: {it.text}"
        for loc, it in checkable
    )
    try:
        out = structured(
            session,
            "guard.claims",
            ClaimCheck,
            {"facts": backing_text(cv), "paragraphs": blocks},
            context={"cv": cv, "paragraphs": {loc: it.text for loc, it in checkable}},
        )
    except ApiError as exc:
        logfire.warn("Couldn't check a document's claims: {code}", code=exc.code)
        return []
    return [
        Violation(location=f.location, rule="claims", detail=f'"{f.claim}" isn\'t in the lines it cites') for f in out.findings
    ]


def guard_and_repair(
    session: Session,
    items: list[tuple[str, Cited]],
    cv: MasterCV,
    keywords: list[str],
    *,
    claims: bool = False,
    unsourced_ok: frozenset[str] | set[str] = frozenset(),
) -> tuple[GuardResult, Decisions]:
    """Checks every line, repairs each failing one once, and reports what is left.

    Returns the guard result to store with the document and, per location, the replacement line
    or `None` for "drop it". A line the person edited themselves is never touched (G2-02).

    `claims` adds the model's claim check to the regex rules — for prose a model composed (the
    letter), not for CV lines, which are copies of the master and can't claim anything it doesn't.
    """
    first = check_cited(items, cv, keywords, unsourced_ok=unsourced_ok)
    if claims and (found := claim_violations(session, items, cv)):
        first = GuardResult(pass_=False, violations=[*first.violations, *found], repaired=False)
    if first.pass_:
        return first, {}

    by_location = dict(items)
    failing = {v.location for v in first.violations}
    decisions: Decisions = {}

    for location in sorted(failing):
        item = by_location.get(location)
        if item is None:
            continue
        if not item.source_ids:
            # Nothing to repair *against*. The prompt's own instruction for this case is "return
            # null", so asking costs a model call to be told what is already known — and since
            # `Cites` started dropping unknown ids rather than rejecting a whole letter for one,
            # a paragraph arriving with no sources is a case that actually happens.
            decisions[location] = None
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
        # `composed=True`, always: a repaired line was rewritten by a model, so whatever it was
        # before, it is no longer a copy of anything. Without this a repair would hand
        # `Store._assert_verbatim` a CV line that doesn't match the master line it cites, and the
        # whole pack would be refused as drifted — the repair destroying the document it fixed.
        decisions[location] = Cited(text=out.item.text, source_ids=out.item.source_ids, composed=True)

    # Re-check what the repairs produced; anything still failing is dropped.
    repaired = [(loc, decisions.get(loc) or by_location[loc]) for loc in sorted(failing) if decisions.get(loc) is not None]
    # The regex rules only: the repair was told which claim to remove, and asking the claim check
    # again would make every repaired paragraph cost two more calls.
    second = (
        check_cited(repaired, cv, keywords, unsourced_ok=unsourced_ok)
        if repaired
        else GuardResult(pass_=True, violations=[], repaired=False)
    )
    for violation in second.violations:
        decisions[violation.location] = None

    dropped = sorted(loc for loc, value in decisions.items() if value is None)
    violations = [Violation(location=loc, rule="dropped", detail=_dropped_detail(loc, first)) for loc in dropped]
    return GuardResult(pass_=not violations, violations=violations, repaired=bool(failing)), decisions


WHY = {
    "numbers": "a number that isn't in the lines it cited",
    "terms": "a skill that isn't in the cited lines or anywhere in your CV",
    "claims": "a claim the lines it cites don't support",
    "sources": "no usable master-CV source",
}


def _dropped_detail(location: str, first: GuardResult) -> str:
    """What the person is told about a line WERA left out, using that line's own first failure."""
    rules = {v.rule for v in first.violations if v.location == location}
    named = next(
        (WHY[rule] for rule in ("numbers", "terms", "claims", "sources") if rule in rules), "a claim your CV doesn't carry"
    )
    return f"Left out: it had {named}, and rewriting it didn't fix that."
