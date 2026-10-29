"""Tables (ARCHITECTURE §5). One schema for SQLite (default, zero setup) and Postgres.

Rows keep the fields WERA queries on as columns and the rest of each contract as JSON, so a
Pydantic model round-trips through one `data` column without a migration per field.
"""

from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Json = JSON().with_variant(JSONB(), "postgresql")
Iso = String(32)  # ISO 8601 UTC timestamps, as JavaScript's toISOString() writes them


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: Json, list[Any]: Json}


class MasterCvRow(Base):
    """Every saved version is kept (CV-05), so packs can say which version they came from."""

    __tablename__ = "master_cv"
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    data: Mapped[dict[str, Any]]
    saved_at: Mapped[str] = mapped_column(Iso)


class ContactRow(Base):
    """CV-06: contact details live apart from the CV and are never sent to a model."""

    __tablename__ = "contact"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(Text)


class ProfileRow(Base):
    __tablename__ = "search_profile"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict[str, Any]]


class SettingsRow(Base):
    """Non-secret settings only. Keys live in the environment (SET-07)."""

    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict[str, Any]]


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(512), unique=True)  # REL-02
    status: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[str | None] = mapped_column(Iso)
    first_seen: Mapped[str] = mapped_column(Iso)
    snoozed_until: Mapped[str | None] = mapped_column(Iso)
    data: Mapped[dict[str, Any]]  # the full Job contract
    questions: Mapped[list[Any]]  # application-form questions (ANS-01)


class FitReportRow(Base):
    """Model output only (sub-scores and evidence). Totals are computed in code on read (MATCH-04)."""

    __tablename__ = "fit_reports"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    evidence: Mapped[dict[str, Any]]
    model: Mapped[str] = mapped_column(String(128))
    cv_version: Mapped[int | None] = mapped_column(Integer)
    # COST-04: with the CV version and the model, this is the whole cache key. A job whose
    # description changed is a different job to score.
    job_hash: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[str] = mapped_column(Iso)


class ApplicationRow(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[str] = mapped_column(Iso)
    draft_started_at: Mapped[str | None] = mapped_column(Iso)
    # Which document the graph is drafting. Phase 1 inferred this from elapsed time; a real
    # node writes it now, and the review screen polls it (PERF-03).
    draft_step: Mapped[str | None] = mapped_column(String(16))
    change_note: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[str | None] = mapped_column(Iso)
    submitted_at: Mapped[str | None] = mapped_column(Iso)
    follow_up_at: Mapped[str | None] = mapped_column(Iso)
    notes: Mapped[str | None] = mapped_column(Text)


class DocumentRow(Base):
    """Pack documents. Every edit is a new version (G2-02); the highest version is current."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("application_id", "kind", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # cv · letter · ats · answers
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]]
    created_at: Mapped[str] = mapped_column(Iso)


class DecisionRow(Base):
    """Every Gate 1 and Gate 2 decision, so weights can be refit later (MATCH-06)."""

    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    gate: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[str] = mapped_column(Iso)


class IntakeRunRow(Base):
    __tablename__ = "intake_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[str] = mapped_column(Iso)
    finished_at: Mapped[str | None] = mapped_column(Iso)
    progress: Mapped[float] = mapped_column(Float, default=0)
    # Which part of the run is happening, and where it is within that part. Scoring is where
    # almost all of a real run's time goes — 40 jobs at ~11s each — and until these existed the
    # screen had nothing to show for it but a sentence that never changed.
    phase: Mapped[str] = mapped_column(String(16), default="searching")
    done: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    queries: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[int] = mapped_column(Integer, default=0)
    new_jobs: Mapped[int] = mapped_column(Integer, default=0)
    credits_used: Mapped[int] = mapped_column(Integer, default=0)
    # SRCH-10: what the crawl cache answered for free, in the same units as `credits_used`. Kept
    # beside it rather than folded into it because the two are different facts — one is what the
    # invoice will say, the other is what it would have said.
    credits_saved: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[Any]]
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)  # demo runs progress over time
    settled: Mapped[bool] = mapped_column(Boolean, default=False)


class LlmCallRow(Base):
    """One row per model call, for the weekly budget (COST-01) and the spend figure (SET-06).

    PRIV-03: metadata only. No prompt text, no completion text, no error body from the
    provider — just the task, the model, token counts, cost, latency and a sanitised code.
    """

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task: Mapped[str] = mapped_column(String(32), index=True)
    tier: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    code: Mapped[str | None] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    called_at: Mapped[str] = mapped_column(Iso, index=True)


class KvRow(Base):
    """Small bits of app state: channel stats, id counter, the last run's new jobs."""

    __tablename__ = "kv"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(Json)


class CrawlCacheRow(Base):
    """SRCH-10: what the last crawl paid Firecrawl for, kept for a day or two.

    Not a store. Every row has an expiry and nothing here is a source of truth — a job that
    matters is in `jobs`, and losing this table costs credits rather than data. It gets its own
    table rather than a `kv` key because the payloads are whole job descriptions and the
    lookups are per URL: one JSON blob would mean reading and rewriting every cached page to
    read one of them.
    """

    __tablename__ = "crawl_cache"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)  # sha256 of the request
    kind: Mapped[str] = mapped_column(String(16), index=True)  # "search" | "card"
    # Always an object, never a bare null: a page WERA read but found no job on is cached as
    # `{"card": null}`, and that has to be distinguishable from a key that isn't here at all.
    payload: Mapped[dict[str, Any]]
    stored_at: Mapped[str] = mapped_column(Iso)
    expires_at: Mapped[str] = mapped_column(Iso, index=True)
