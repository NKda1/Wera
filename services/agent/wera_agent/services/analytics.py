"""The analytics schema's one table, `analytics.fit_totals` (OBS-03).

Everything else in `analytics` is a view (migration 0011). A fit total can't be: it is computed
in code from the sub-scores, the weights and the posting's age (MATCH-04), and SQL that
re-implemented the weights would disagree with the screen the first time a weight changed. So
the totals are copied in by the same `Store.scored` the Jobs screen reads, after each scoring
pass and on `wera analytics refresh`, and stamped with when — freshness, and so the total, is a
claim about the day it was computed.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import logfire
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, delete, insert, select, text
from sqlalchemy.orm import Session

from ..clock import parse_ms
from ..db.models import FitReportRow, JobRow
from ..db.session import engine, transaction

FIT_TOTALS = Table(
    "fit_totals",
    MetaData(schema="analytics"),
    Column("job_id", String(64), primary_key=True),
    Column("total", Float),
    Column("core", Float),
    Column("adjacent", Float),
    Column("experience", Float),
    Column("eligibility", Float),
    Column("freshness", Float),
    Column("rough_only", Boolean),
    Column("stale", Boolean),
    Column("model", String(128)),
    Column("cv_version", Integer),
    Column("scored_at", DateTime(timezone=True)),
    Column("refreshed_at", DateTime(timezone=True)),
)


def refresh_fit_totals(session: Session) -> int:
    """Replace `analytics.fit_totals` with every scored job's current total. Postgres only."""
    from .store import Store

    if session.get_bind().dialect.name != "postgresql":
        return 0
    store = Store(session)
    weights = store.weights()
    refreshed_at = datetime.fromtimestamp(store.now / 1000, UTC)
    rows = []
    for job_row, fit_row in session.execute(select(JobRow, FitReportRow).join(FitReportRow, FitReportRow.job_id == JobRow.id)):
        fit = store.scored(store._job(job_row), weights).fit
        rows.append(
            {
                "job_id": job_row.id,
                "total": fit.total,
                "core": fit.core.score,
                "adjacent": fit.adjacent.score,
                "experience": fit.experience.score,
                "eligibility": fit.eligibility.score,
                "freshness": fit.freshness,
                "rough_only": fit.rough_only,
                "stale": fit.stale,
                "model": fit_row.model,
                "cv_version": fit_row.cv_version,
                "scored_at": datetime.fromtimestamp(parse_ms(fit_row.created_at) / 1000, UTC),
                "refreshed_at": refreshed_at,
            }
        )
    session.execute(delete(FIT_TOTALS))
    if rows:
        session.execute(insert(FIT_TOTALS), rows)
    return len(rows)


@dataclass(frozen=True)
class OpenRouterUsage:
    """OpenRouter's own record of the calls on WERA's key (OBS-06), as archived from Logfire."""

    calls: int
    cost_usd: float
    by_environment: dict[str, float]  # "real", "eval", "demo", or "other" for anything else on the key
    last: datetime | None


def openrouter_usage(since: datetime) -> OpenRouterUsage | None:
    """What OpenRouter billed since `since`. None where `analytics.openrouter_calls` doesn't exist —
    SQLite, or a database migration 0014 hasn't reached."""
    with engine().connect() as conn:
        if (
            conn.dialect.name != "postgresql"
            or not conn.exec_driver_sql("select to_regclass('analytics.openrouter_calls')").scalar()
        ):
            return None
        rows = conn.execute(
            text(
                "select coalesce(environment, 'other'), count(*), coalesce(sum(cost_usd), 0), max(called_at)"
                " from analytics.openrouter_calls where called_at >= :since group by 1"
            ),
            {"since": since},
        ).all()
    return OpenRouterUsage(
        calls=sum(r[1] for r in rows),
        cost_usd=float(sum(r[2] for r in rows)),
        by_environment={r[0]: float(r[2]) for r in rows},
        last=max((r[3] for r in rows), default=None),
    )


def refresh_quietly() -> None:
    """After a scoring pass. Analytics must never fail the run that fed it."""
    try:
        with transaction() as session:
            refresh_fit_totals(session)
    except Exception:  # noqa: BLE001 — logged; the run's own results are already committed
        logfire.exception("Couldn't refresh analytics.fit_totals")
