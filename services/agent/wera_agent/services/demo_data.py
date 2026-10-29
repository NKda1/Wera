"""Demo fixtures (fixtures/demo), shared by the seed and the demo intake run."""

import json
from typing import Any

from ..clock import iso
from ..domain.text import slug
from ..paths import FIXTURES_DIR
from ..schemas.jobs import FitEvidence, Job

DAY = 24 * 60 * 60 * 1000
MIN = 60 * 1000


def fixture(name: str) -> Any:
    return json.loads((FIXTURES_DIR / "demo" / name).read_text(encoding="utf-8"))


def seed_to_job(s: dict[str, Any], now: int, status: str | None = None) -> tuple[Job, FitEvidence, list[str]]:
    # A seed with no `postedDaysAgo` is a job whose source never said when it was posted —
    # common on a LinkedIn alert card, and the one case the urgency groups must not guess at.
    # `firstSeen` still needs a time, so it falls back to when the demo was seeded.
    days = s.get("postedDaysAgo")
    posted = now - (days if days is not None else 0) * DAY
    ats = s.get("ats")
    dedupe = (
        f"ats:{ats['vendor']}:{ats['boardToken']}:{ats['jobId']}"
        if ats
        else f"hash:{slug(s['company'])}:{slug(s['title'])}:{slug(s['city'])}"
    )
    job = Job.model_validate(
        {
            "id": s["id"],
            "dedupeKey": dedupe,
            "title": s["title"],
            "company": s["company"],
            "location": {"city": s["city"], "country": s["country"], "raw": f"{s['city']}, {s['country']}"},
            "remote": s.get("remote"),
            "salary": s.get("salary"),
            "descriptionMd": s.get("description"),
            "descriptionStatus": "full" if s.get("description") else "missing",
            "applyUrl": s.get("applyUrl"),
            "ats": ats,
            "linkedinUrl": s.get("linkedinUrl"),
            "sources": [{**src, "seenAt": iso(posted + (i + 1) * 3 * 60 * MIN)} for i, src in enumerate(s["sources"])],
            "language": "en",
            "postedAt": iso(posted) if days is not None else None,
            # The same rule a real run applies (`normalise.from_feed`): only the employer's own
            # board gives a real timestamp. The demo has to show that split or the urgency
            # groups would look more certain than they are.
            "postedPrecision": (
                "unknown"
                if days is None
                else "exact"
                if any(src["channel"] == "watchlist" for src in s["sources"])
                else "approximate"
            ),
            "firstSeen": iso(posted + 2 * 60 * MIN),
            "lastSeen": iso(now),
            "status": status or s.get("status") or "new",
            "hiddenReason": s.get("hiddenReason"),
            "seniority": s.get("seniority"),
        }
    )
    # A hidden job gets no fit evidence, because a real run never produces any: MATCH-01
    # filters it out before the first model call. The demo used to attach evidence to every
    # job regardless, which made demo mode the one place where every job has a score — so the
    # Jobs screen passed every test and then 500'd on the first real database, where 3,223
    # hidden jobs had nothing to read.
    evidence = None if job.status == "hidden" else FitEvidence.model_validate(s["evidence"])
    return job, evidence, list(s.get("questions") or [])


def first_run_seeds() -> list[dict[str, Any]]:
    return fixture("jobs.json")["firstRun"]
