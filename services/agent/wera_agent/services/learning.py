"""Learning from a person's Gate 1 decisions (MATCH-11…13, 15 Nov).

Before this, every decision was saved (MATCH-06) and nothing read it back: someone could reject
three security roles as "Not my field" and the fourth would arrive ranked exactly where the
first had. All of this is computed in code from the `decisions` table, with no model call, and
none of it changes a fit total (MATCH-04) or the profile on its own (PRF-04):

* **Ranking (MATCH-12).** The words a person's rejected titles share — and the ones their
  approvals share — move a job up or down the shortlist by at most `MAX_SHIFT`, with the reason
  on the job. The words of their own target titles are left out: they asked for those, so a
  rejection can't be about them, and every job carries them.
* **Suggestions (MATCH-13).** Changes the decisions point at — an exclusion, a title to drop or
  add, a place to add — each proposed, never applied, until the person accepts it.

Per person, with decisions from earlier CV versions counted at half weight: a CV switch changes
what fits, not what someone wants to do. The counts are a plain table (word → rejections and
approvals per reason) so that, once accounts exist, people with a similar CV can share them as a
starting point (MATCH-14, phase 3). Nothing here is personal: a decision's features are a job's,
not the person's.
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import DecisionRow
from ..domain import filters
from ..schemas.jobs import Job, ScoredJob
from ..schemas.learning import LearnedRank, ReasonCode, Suggestion
from ..schemas.profile import MAX_TITLES, SearchProfile

# The reject dialog's chips, as they were sent before codes existed (and still are, by an old tab).
REASON_FROM_NOTE: dict[str, ReasonCode] = {
    "Wrong level": "wrong_level",
    "Wrong location": "wrong_location",
    "Not my field": "not_my_field",
    "Pay too low": "pay_too_low",
    "Already applied elsewhere": "applied_elsewhere",
}
REASON_LABEL = {code: label for label, code in REASON_FROM_NOTE.items()}

# Reasons that are about what the job *is*, and so what its title's words can teach. A rejection
# for pay, place or "applied elsewhere" says nothing about the work.
ABOUT_THE_WORK: frozenset[str] = frozenset({"not_my_field", "wrong_level"})
POSITIVE = frozenset({"approve", "unhide"})

# A word needs this much weight of decisions behind it before it moves anything: one rejection is
# an opinion about one job.
MIN_EVIDENCE = 2.0
# Points of fit total per unit of log-odds. Three rejections and no approvals ≈ −0.5; the cap is
# what keeps a learned dislike from burying a 9/10 job under a 6.
SCALE = 0.35
MAX_SHIFT = 1.0
# Earlier CVs' decisions still count, for less: the person is the same, the CV isn't.
OLDER_CV_WEIGHT = 0.5

# Suggestion thresholds. Higher than ranking's, because accepting one hides jobs or changes searches.
EXCLUDE_AFTER = 3
REMOVE_TITLE_AFTER = 3
ADD_TITLE_AFTER = 2
# A title WERA nearly matched (UPSK-06) at least this many times, from this many employers, at
# this closeness to the CV: "Private Equity Associate" for a finance CV, 0.72 by meaning against
# a target title's 0.80 (20 Nov). Offered as a title to add, never added on its own.
NEAR_TITLE_AFTER = 3
NEAR_TITLE_MIN_SIMILARITY = 0.72
ADD_LOCATION_AFTER = 2

DISMISSED_KEY = "learningDismissed"
LEARNED_EXCLUDES_KEY = "learnedExcludes"


def reason_code(code: str | None, note: str | None) -> ReasonCode | None:
    """The coded reason for a decision, from the code sent or, failing that, a preset's text."""
    if code:
        return code  # type: ignore[return-value] — validated by `DecisionIn`
    return REASON_FROM_NOTE.get((note or "").strip())


def features_of(job: Job, scored: ScoredJob | None) -> dict[str, Any]:
    """What a decision remembers about its job: enough to learn from once the job row is gone.

    The job's own facts only — title words, scores, place, pay, source — never the person's.
    """
    salary = job.salary
    return {
        "title": job.title,
        "tokens": sorted(filters.title_tokens(job.title)),
        "company": job.company,
        "via": job.via,
        "city": job.location.city,
        "country": job.location.country,
        "remote": job.remote,
        "seniority": job.seniority,
        "hiddenReason": job.hidden_reason,
        "salary": salary.model_dump(mode="json") if salary else None,
        "scores": (
            {k: getattr(scored.fit, k).score for k in ("core", "adjacent", "experience", "eligibility")} if scored else None
        ),
    }


@dataclass
class Word:
    against: float = 0.0  # weight of rejections about the work
    for_: float = 0.0  # weight of approvals and unhides
    reasons: Counter = field(default_factory=Counter)  # rejections by reason, counted whole

    def log_odds(self) -> float:
        return math.log((self.against + 1) / (self.for_ + 1))


@dataclass
class Learned:
    words: dict[str, Word]
    decisions: int
    # Pay rejected as too low, as the highest figure rejected, per (currency, period).
    pay_floor: dict[tuple[str, str], float]
    # Titles approved or brought back that no target covers, and places brought back from hiding.
    approved_titles: Counter
    unhidden_places: Counter
    rejected_titles: list[tuple[str, str]]  # (title, reason) for rejections about the work
    approved_title_list: list[str]


def learn(session: Session, cv_version: int, profile: SearchProfile) -> Learned:
    """Everything the Gate 1 decisions say, as counts. Cheap enough to run per request."""
    target_words = set().union(*(filters.title_tokens(t) for t in profile.titles if t.strip())) if profile.titles else set()
    words: dict[str, Word] = defaultdict(Word)
    pay_floor: dict[tuple[str, str], float] = {}
    approved_titles: Counter = Counter()
    unhidden_places: Counter = Counter()
    rejected_titles: list[tuple[str, str]] = []
    approved_title_list: list[str] = []
    # Gate 1's decisions, and Gate 2's discards: throwing a finished pack away is a "no" to the job,
    # read as a reject (19 Nov). A cancelled draft (`cancel`) says nothing about the job.
    gate_1 = DecisionRow.gate == 1
    discarded = (DecisionRow.gate == 2) & (DecisionRow.decision == "discard")
    rows = session.scalars(select(DecisionRow).where(gate_1 | discarded, DecisionRow.features.is_not(None))).all()
    counted = 0
    for row in rows:
        f = row.features or {}
        weight = 1.0 if row.cv_version == cv_version else OLDER_CV_WEIGHT
        tokens = set(f.get("tokens") or []) - target_words
        code = row.reason_code
        decision = "reject" if row.decision == "discard" else row.decision
        if decision == "reject" and code in ABOUT_THE_WORK:
            counted += 1
            rejected_titles.append((f.get("title") or "", code))
            for t in tokens:
                words[t].against += weight
                words[t].reasons[code] += 1
        elif decision in POSITIVE:
            counted += 1
            for t in tokens:
                words[t].for_ += weight
            title = f.get("title") or ""
            approved_title_list.append(title)
            if title and not filters.matches_a_target(title, profile.titles):
                approved_titles[_title_key(title)] += 1
            if decision == "unhide" and (f.get("hiddenReason") or "").startswith("Location") and f.get("city"):
                unhidden_places[f["city"]] += 1
        elif decision == "reject" and code == "pay_too_low" and f.get("salary"):
            counted += 1
            s = f["salary"]
            top = s.get("max") or s.get("min")
            if top:
                key = (s.get("currency") or "", s.get("period") or "")
                pay_floor[key] = max(pay_floor.get(key, 0.0), float(top))
        elif decision == "reject":
            counted += 1
    return Learned(
        words=dict(words),
        decisions=counted,
        pay_floor=pay_floor,
        approved_titles=approved_titles,
        unhidden_places=unhidden_places,
        rejected_titles=rejected_titles,
        approved_title_list=approved_title_list,
    )


def adjust(job: ScoredJob, learned: Learned) -> LearnedRank | None:
    """MATCH-12: how far this job moves in the order, and the reason shown for it. None if not."""
    shift = 0.0
    strongest: tuple[float, str, Word] | None = None
    for t in filters.title_tokens(job.title):
        w = learned.words.get(t)
        if w is None or w.against + w.for_ < MIN_EVIDENCE:
            continue
        contribution = -SCALE * w.log_odds()
        shift += contribution
        if strongest is None or abs(contribution) > abs(strongest[0]):
            strongest = (contribution, t, w)
    pay_reason = None
    if job.salary:
        floor = learned.pay_floor.get((job.salary.currency, job.salary.period))
        top = job.salary.max or job.salary.min
        if floor and top and top <= floor:
            shift -= 0.5
            pay_reason = "pay at or below what you rejected as too low"
    shift = max(-MAX_SHIFT, min(MAX_SHIFT, shift))
    if abs(shift) < 0.05:
        return None
    if strongest is not None and abs(strongest[0]) >= 0.05:
        contribution, word, w = strongest
        if contribution < 0:
            code, count = w.reasons.most_common(1)[0] if w.reasons else ("not_my_field", int(w.against))
            label = REASON_LABEL.get(code, code).lower()
            reason = f"Ranked lower: like {count} you rejected as {label} ({word})"
        else:
            reason = f"Ranked higher: like {_count(w.for_)} you approved ({word})"
        if pay_reason:
            reason += f"; {pay_reason}"
    else:
        reason = f"Ranked lower: {pay_reason}" if pay_reason else "Ranked by your past decisions"
    return LearnedRank(adjustment=round(shift, 2), reason=reason)


def suggestions(learned: Learned, profile: SearchProfile, dismissed: set[str]) -> list[Suggestion]:
    """MATCH-13: the profile changes the decisions point at, strongest first. None is applied."""
    out: list[Suggestion] = []
    excluded = {t.lower().strip() for t in profile.exclude}

    # A word only ever in titles rejected as "not my field" — and never in one approved.
    for word, w in sorted(learned.words.items(), key=lambda kv: -kv[1].reasons["not_my_field"]):
        n = w.reasons["not_my_field"]
        if n >= EXCLUDE_AFTER and w.for_ == 0 and word not in excluded and len(word) > 2:
            term = _as_written(word, [t for t, _ in learned.rejected_titles])
            out.append(
                Suggestion(
                    id=f"exclude_term:{word}",
                    kind="exclude_term",
                    value=term,
                    message=f"Hide jobs with “{term}” in the title — you rejected {n} as not your field and approved none.",
                    evidence=n,
                )
            )

    # A target title whose jobs are rejected about the work and never approved.
    for title in profile.titles:
        rejected = [t for t, _ in learned.rejected_titles if filters.matches_a_target(t, [title])]
        approved = [t for t in learned.approved_title_list if filters.matches_a_target(t, [title])]
        if len(rejected) >= REMOVE_TITLE_AFTER and not approved and len(profile.titles) > 1:
            out.append(
                Suggestion(
                    id=f"remove_title:{title.lower()}",
                    kind="remove_title",
                    value=title,
                    message=f"Stop searching for “{title}” — you rejected {len(rejected)} of its jobs and approved none.",
                    evidence=len(rejected),
                )
            )

    # Titles approved or brought back that no target covers.
    for key, n in learned.approved_titles.most_common():
        if n >= ADD_TITLE_AFTER and len(profile.titles) < MAX_TITLES:
            title = next(t for t in learned.approved_title_list if _title_key(t) == key)
            out.append(
                Suggestion(
                    id=f"add_title:{key}",
                    kind="add_title",
                    value=_clean_title(title),
                    message=f"Also search for “{_clean_title(title)}” — you approved {n} jobs like it.",
                    evidence=n,
                )
            )

    # Places brought back from "Location isn't in your search".
    have = {loc.split(",")[0].strip().lower() for loc in profile.locations}
    for place, n in learned.unhidden_places.most_common():
        if n >= ADD_LOCATION_AFTER and place.lower() not in have and len(profile.locations) < 8:
            out.append(
                Suggestion(
                    id=f"add_location:{place.lower()}",
                    kind="add_location",
                    value=place,
                    message=f"Add {place} to your locations — you brought back {n} jobs hidden for being there.",
                    evidence=n,
                )
            )

    return [s for s in out if s.id not in dismissed]


def near_miss_titles(session: Session, profile: SearchProfile, dismissed: set[str]) -> list[Suggestion]:
    """Titles WERA keeps nearly matching, offered as titles to search for (20 Nov).

    Near misses (`job_signals`, UPSK-06) are jobs the title rule dropped though the CV is close to
    them. A kind of title seen from `NEAR_TITLE_AFTER` employers is one the person may simply not
    have thought to name — and adding it is what moves those jobs from Upskills onto the list.
    """
    from ..db.models import JobSignalRow
    from ..domain import dedupe

    if len(profile.titles) >= MAX_TITLES:
        return []
    rows = session.scalars(select(JobSignalRow).where(JobSignalRow.similarity >= NEAR_TITLE_MIN_SIMILARITY)).all()
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        title = _clean_title(dedupe.bare_title(row.company, row.title))
        key = _title_key(title)
        if not key or filters.matches_a_target(title, profile.titles):
            continue
        entry = seen.setdefault(key, {"titles": Counter(), "companies": set()})
        entry["titles"][title] += 1
        entry["companies"].add(dedupe.employer_key(row.company, row.title))
    out: list[Suggestion] = []
    for key, entry in sorted(seen.items(), key=lambda kv: -len(kv[1]["companies"])):
        n = len(entry["companies"])
        if n < NEAR_TITLE_AFTER or f"add_title:{key}" in dismissed:
            continue
        title = entry["titles"].most_common(1)[0][0]
        out.append(
            Suggestion(
                id=f"add_title:{key}",
                kind="add_title",
                value=title,
                message=f"Also search for “{title}” — WERA nearly matched {n} jobs like it, from {n} employers.",
                evidence=n,
            )
        )
    return out


def apply_suggestion(profile: SearchProfile, suggestion: Suggestion) -> SearchProfile:
    """The profile with one accepted suggestion in it."""
    if suggestion.kind == "exclude_term":
        return profile.model_copy(update={"exclude": [*profile.exclude, suggestion.value]})
    if suggestion.kind == "remove_title":
        return profile.model_copy(update={"titles": [t for t in profile.titles if t != suggestion.value]})
    if suggestion.kind == "add_title":
        return profile.model_copy(update={"titles": [*profile.titles, suggestion.value]})
    return profile.model_copy(update={"locations": [*profile.locations, suggestion.value]})


def _count(weight: float) -> int:
    return max(1, round(weight))


def _title_key(title: str) -> str:
    """One key per kind of title: "Graduate Data Engineer - London" and "Graduate Data Engineer"
    are the same title to suggest."""
    return " ".join(sorted(filters.title_tokens(_clean_title(title))))


def _clean_title(title: str) -> str:
    """A posting's title as a search title: its main part, without brackets or a trailing place."""
    main = filters._BRACKETS.sub(" ", title).split(" - ")[0].split(",")[0].split("|")[0]
    return " ".join(main.split())


def _as_written(word: str, titles: list[str]) -> str:
    """A title word the way the rejected postings wrote it ("Security", not "security")."""
    for title in titles:
        for raw in title.replace("/", " ").split():
            if raw.strip("(),.-").lower().rstrip("s") == word.rstrip("s"):
                return raw.strip("(),.-")
    return word.title()
