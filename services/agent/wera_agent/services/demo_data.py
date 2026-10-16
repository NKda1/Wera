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
    posted = now - s["postedDaysAgo"] * DAY
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
            "postedAt": iso(posted),
            "firstSeen": iso(posted + 2 * 60 * MIN),
            "lastSeen": iso(now),
            "status": status or s.get("status") or "new",
            "hiddenReason": s.get("hiddenReason"),
            "seniority": s.get("seniority"),
        }
    )
    return job, FitEvidence.model_validate(s["evidence"]), list(s.get("questions") or [])


def first_run_seeds() -> list[dict[str, Any]]:
    return fixture("jobs.json")["firstRun"]
