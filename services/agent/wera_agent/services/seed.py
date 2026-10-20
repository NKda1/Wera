"""Demo seed (DEMO-01): the fictional CV, profile, jobs and packs from fixtures/demo.

Seed jobs carry relative dates (postedDaysAgo), resolved against "now" when seeding, so the
demo always looks like today's shortlist.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import iso
from ..config import companies, wera
from ..db.models import ContactRow, MasterCvRow, ProfileRow, SettingsRow
from ..schemas.cv import MasterCV
from ..settings import get_settings
from . import store as st
from .demo_data import DAY, MIN, fixture, seed_to_job
from .drafting import draft_pack


def is_seeded(session: Session) -> bool:
    return session.scalar(select(MasterCvRow.version).limit(1)) is not None


def seed(session: Session, now: int) -> None:
    """Fill an empty database with the demo data. Real (non-demo) databases start from the
    same CV and profile so the screens work; the user replaces them on the CV page."""
    cfg = wera()
    settings = get_settings()
    local = datetime.fromtimestamp(now / 1000).astimezone()
    ran = local.replace(hour=8, minute=2, second=0, microsecond=0)
    ran_ms = int(ran.timestamp() * 1000)
    if ran_ms > now:
        ran_ms -= DAY

    cv = MasterCV.model_validate(fixture("cv.json"))
    session.add(MasterCvRow(version=cv.version, data=cv.model_dump(mode="json"), saved_at=iso(now)))
    contact = fixture("contact.json")
    session.add(ContactRow(id=1, email=contact["email"], phone=contact.get("phone"), address=contact.get("address")))
    session.add(ProfileRow(id=1, data={**fixture("profile.json"), "updatedAt": iso(now)}))
    session.add(
        SettingsRow(
            id=1,
            data={
                "redactContact": True,
                "weeklyBudgetUsd": cfg.weekly_budget_usd,
                "spentThisWeekUsd": 1.84 if settings.wera_demo_mode else 0,
                "cvPages": cfg.cv_pages,
                "baseUrl": settings.llm_base_url,
                "models": cfg.models.model_dump(),
                "weights": cfg.weights.model_dump(),
                "inbox": {
                    "label": settings.alerts_imap_user or "[your-alerts-mailbox]",
                    "folder": settings.alerts_folder,
                    "lastCheckedAt": iso(now - 10 * MIN) if settings.wera_demo_mode else None,
                    "emailsThisWeek": 23 if settings.wera_demo_mode else 0,
                    "senders": ["LinkedIn job alerts", "Indeed", "Bright Network", "Gradcracker"],
                },
            },
        )
    )
    # The channel strip on the shortlist reads these. A real database starts at zero with no
    # timestamps — "not run yet" — and the first run fills them in; the demo starts mid-week so
    # the screens have something to show.
    st.set_kv(
        session,
        "stats",
        {
            "board": {"queries": 20, "results": 126, "ranAt": iso(ran_ms)},
            "inbox": {"emails": 23, "jobs": 71, "checkedAt": iso(now - 10 * MIN)},
            "watchlist": {"companies": 32, "newSinceYesterday": 17, "checkedAt": iso(ran_ms)},
        }
        if settings.wera_demo_mode
        else {
            "board": {"queries": 0, "results": 0, "ranAt": None},
            "inbox": {"emails": 0, "jobs": 0, "checkedAt": None},
            "watchlist": {"companies": len(companies()), "newSinceYesterday": 0, "checkedAt": None},
        },
    )
    st.set_kv(session, "firstRunDelivered", False)
    st.set_kv(session, "lastRunJobIds", [])
    st.set_kv(session, "counter", 0)
    session.flush()

    jobs = fixture("jobs.json")
    # Demo jobs are demo-mode only. A fictional vacancy on a real shortlist is indistinguishable
    # from a real one — it carries a fit score, an apply URL and a company that don't exist — so
    # a real database starts with an empty list and fills it on the first run. The CV and profile
    # above are different: they are a starting point the person edits, not a claimed finding.
    for s in [*jobs["shortlist"], *jobs["hidden"]] if settings.wera_demo_mode else []:
        st.save_job(session, *seed_to_job(s, now), created_at=iso(now))

    store = st.Store(session, now)
    # Demo applications come with drafted packs, and drafting goes through the gateway — which
    # outside demo mode means real model calls on fixture data, charged to the person's budget
    # the first time they start WERA for real. So these belong to demo mode only.
    for a in jobs["applications"] if settings.wera_demo_mode else []:
        job, evidence, questions = seed_to_job(a["job"], now, "closed" if a["status"] == "rejected" else "in_application")
        st.save_job(session, job, evidence, questions, created_at=iso(now))
        created = now - a["daysAgo"] * DAY
        app_id = f"app-{job.id[4:]}"
        submitted = a.get("submittedDaysAgo")
        follow = a.get("followUpInDays")
        store.create_application(
            app_id,
            job,
            status=a["status"],
            created_at=iso(created),
            approved_at=iso(created + 2 * 60 * MIN) if a["status"] != "ready_for_review" else None,
            submitted_at=iso(now - submitted * DAY) if submitted is not None else None,
            follow_up_at=(iso(now + follow * DAY) if follow is not None else iso(now - submitted * DAY + 7 * DAY))
            if submitted is not None
            else None,
            notes=a.get("notes"),
        )
        # The demo starts with finished packs, drafted by the same steps the graph runs, so the
        # review screen has something to show before anyone approves anything.
        draft_pack(session, app_id, ready=a["status"] == "ready_for_review")
