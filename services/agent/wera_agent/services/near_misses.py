"""Near misses for Upskills (UPSK-06, 20 Nov): what the jobs a CV *nearly* matched ask for.

Upskills read only the jobs WERA scored, and a search scores the dozen or so it shows. On 20 Nov a
finance search found 606 postings, and the title rule dropped 274 of them unread — "Equity
Analyst", "Private Equity Associate", "Corporate Credit Research Analyst" among them. Those are
the jobs a person a step away from their targets would apply to next, and what they ask for is
exactly what Upskills is for.

**What counts.** A job dropped for its title alone: an API lead `strategist.discover_feeds` turned
down as not a target title (`FeedDiscovery.near_misses`), or a saved job hidden as "Not one of
your target titles" or "Close to your titles". A job with an excluded word or a title above the
person's level is not a near miss: that is a job they said no to, not one like theirs.

**Which of them.** All are ranked by `prefilter.lead_ranker` — the title and the first words of
the ad against the CV, by the local embedding, free — and kept only at or above
`upskills.near_miss_min_similarity`. The best `upskills.near_miss_per_search` not already read
are read, each with one fast-tier `keywords.extract` (about $0.0001), several at once.

**What is read.** A saved job's whole description; a full-text API's whole ad (Reed's details
call, USAJOBS' card — free); else the API's snippet, which is short but is the ad's own words. The
extraction keeps only terms the text contains, so a snippet gives fewer keywords, never invented
ones: on 20 Nov, 23 of 40 snippets gave none, which is why Reed's are read in full.

**Stored once** in `job_signals`, one row a posting: a key already there is not read again.
"""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import logfire
from sqlalchemy import select

from ..clock import iso
from ..config import wera
from ..db.models import JobRow, JobSignalRow
from ..db.session import engine, transaction
from ..domain.filters import NEAR_MISS
from ..errors import ApiError
from ..schemas.cv import MasterCV
from ..schemas.jobs import Job

if TYPE_CHECKING:
    from ..nodes.strategist import NearMiss
    from ..sources.targets import Ranker

DAY_MS = 24 * 60 * 60 * 1000
# Saved jobs hidden for their title, looked at as near misses when first seen this recently: the
# same window Upskills reads (`services.upskills.WINDOW_DAYS`).
SAVED_WINDOW_DAYS = 60
# How much of a saved job's ad is ranked with its title: about an API snippet's length, so a saved
# job and a lead are scored on the same scale.
RANKED_CHARS = 500
# The reasons a saved job was hidden for its title (`filters.hidden_reason`).
TITLE_REASONS = ("Not one of your target titles", NEAR_MISS)


@dataclass
class Candidate:
    key: str
    url: str | None
    title: str
    company: str
    text: str
    source: str  # "lead" (an API's snippet), "feed" (an API's full text) or "saved"
    job_id: str | None = None
    score: float | None = None
    job: Job | None = None
    feed: str | None = None
    payload: dict[str, Any] | None = None


@dataclass
class Captured:
    """What one search's near-miss pass did, for the run's log and its report."""

    candidates: int = 0
    above_floor: int = 0
    already_read: int = 0
    extracted: int = 0
    failed: int = 0
    seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


def key_for(url: str | None, fallback: str) -> str:
    from ..sources.targets import url_key

    return url_key(url) if url else hashlib.sha256(fallback.encode()).hexdigest()[:24]


def from_leads(near: list["NearMiss"]) -> list[Candidate]:
    return [
        Candidate(
            key_for(n.url, f"{n.company}|{n.title}"),
            n.url,
            n.title,
            n.company,
            n.snippet,
            "lead",
            score=n.score,
            feed=n.feed,
            payload=n.payload,
        )  # fmt: skip
        for n in near
        if n.title.strip() and (n.snippet.strip() or n.payload)
    ]


def full_text(c: Candidate, redis: Any | None) -> str | None:
    """A full-text API's whole ad for a lead, or None: Reed's details call (through the guard, so
    a posting read for a search's own list is not asked twice), USAJOBS' card."""
    payload = c.payload or {}
    try:
        if c.feed == "reed" and "jobId" in payload and redis is not None:
            from ..sources.feeds import reed
            from ..sources.provider_guard import Guard

            card = reed.read(payload, Guard(redis, "reed"))
        else:
            card = payload.get("card") if isinstance(payload.get("card"), dict) else None
    except ApiError:
        return None
    text = str((card or {}).get("description") or "").strip()
    return text if len(text) >= 200 else None


def from_saved(session: Any, now: int, *, days: int = SAVED_WINDOW_DAYS) -> list[Candidate]:
    """Saved jobs hidden for their title, first seen in the window, with something to read."""
    since = iso(now - days * DAY_MS)
    rows = session.scalars(select(JobRow).where(JobRow.status == "hidden", JobRow.first_seen >= since)).all()
    out: list[Candidate] = []
    for row in rows:
        reason = str((row.data or {}).get("hiddenReason") or "")
        text = str((row.data or {}).get("descriptionMd") or "")
        if not reason.startswith(TITLE_REASONS) or len(text) < 200:
            continue
        job = Job.model_validate(row.data)
        out.append(
            Candidate(
                key_for(job.apply_url, job.dedupe_key), job.apply_url, job.title, job.company, text, "saved", job.id, job=job
            )
        )
    return out


def choose(
    candidates: list[Candidate], *, rank: "Ranker | None", stored: set[str], floor: float, cap: int
) -> tuple[list[Candidate], int, int]:
    """The best `cap` candidates at or above `floor` not already read: (chosen, above the floor,
    already read). Without a ranker nothing is chosen — similarity is the gate, and a guess at it
    would read the wrong jobs."""
    if rank is None or cap <= 0 or not candidates:
        return [], 0, 0
    unscored = [c for c in candidates if c.score is None]
    if unscored:
        for c, score in zip(unscored, rank([(c.title, c.text[:RANKED_CHARS]) for c in unscored]), strict=True):
            c.score = score
    best: dict[str, Candidate] = {}
    for c in sorted(candidates, key=lambda c: -(c.score or 0.0)):
        if (c.score or 0.0) >= floor:
            best.setdefault(c.key, c)  # a saved job and its lead are one posting
    above = list(best.values())
    fresh = [c for c in above if c.key not in stored]
    return fresh[:cap], len(above), len(above) - len(fresh)


def capture(near: list["NearMiss"], *, cv: MasterCV, rank: "Ranker | None", now: int, redis: Any | None = None) -> Captured:
    """UPSK-06 for one search: choose this search's near misses and read the new ones."""
    from ..nodes import fit
    from ..sources import normalise

    cfg = wera().upskills
    out = Captured()
    if cfg.near_miss_per_search <= 0:
        return out
    started = time.monotonic()
    with transaction() as session:
        candidates = [*from_leads(near), *from_saved(session, now)]
        stored = set(session.scalars(select(JobSignalRow.key)).all())
    out.candidates = len(candidates)
    chosen, out.above_floor, out.already_read = choose(
        candidates, rank=rank, stored=stored, floor=cfg.near_miss_min_similarity, cap=cfg.near_miss_per_search
    )

    def read(c: Candidate) -> bool:
        if c.job is None and c.payload and (whole := full_text(c, redis)):
            c.text, c.source = whole, "feed"
        if not c.text.strip():
            return False
        job = c.job or normalise.from_pasted(c.text, title=c.title, company=c.company, location_raw=None, now=now)
        try:
            with transaction() as session:
                asks = fit.keywords_for(session, job, cv)
                session.merge(
                    JobSignalRow(
                        key=c.key,
                        url=c.url,
                        job_id=c.job_id,
                        title=c.title[:300],
                        company=c.company[:300],
                        source=c.source,
                        similarity=round(c.score, 4) if c.score is not None else None,
                        keywords=[k.model_dump(mode="json") for k in asks.keywords],
                        education=asks.education.model_dump(mode="json") if asks.education else None,
                        job_hash=fit.job_hash(job),
                        seen_at=iso(now),
                    )
                )
        except ApiError as exc:
            out.errors.append(exc.code)
            return False
        return True

    workers = 1 if engine().dialect.name == "sqlite" else max(1, cfg.near_miss_workers)
    with logfire.span("near misses", candidates=out.candidates, chosen=len(chosen)) as span:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="near-miss") as pool:
            done = list(pool.map(read, chosen))
        out.extracted, out.failed = sum(done), len(done) - sum(done)
        out.seconds = round(time.monotonic() - started, 1)
        span.set_attributes(
            {"above_floor": out.above_floor, "already_read": out.already_read, "extracted": out.extracted, "failed": out.failed}
        )
    return out
