"""The Postgres schema for Supabase, generated from the same models SQLite uses.

Hand-written SQL would drift from `db/models.py` the first time a column changed, so the tables
come straight out of SQLAlchemy's Postgres dialect — `Json` already declares its `JSONB`
variant, so `jsonb` and `varchar(n)` come out of the models themselves.

Three things are added that the models don't know about, because they belong to Supabase rather
than to the schema:

* **Row-level security on every table, with no policy.** Supabase publishes every table in
  `public` through PostgREST, reachable with the publishable (anon) key that ships in the web
  app's bundle. RLS on with no policy means those keys can read nothing; the agent connects
  with the service role, which bypasses RLS. Until WERA is multi-user (ARCHITECTURE §5 puts
  `users` in phase 3) this is the correct posture: a person's CV, shortlist and letters are not
  public, and the way to keep them private is to grant nobody access rather than to trust that
  no key leaks.
* **pgvector**, for the embeddings the pre-filter computes (MATCH-05). `nodes/prefilter.py`
  recomputes them per run against SQLite; on Postgres they are worth keeping.
* **LangGraph's checkpoint tables are deliberately absent.** `AsyncPostgresSaver.setup()` owns
  them and their shape belongs to the library version in use, so writing them here would mean
  maintaining someone else's migration.
"""

from pathlib import Path

from sqlalchemy.schema import CreateIndex, CreateTable

from .db.models import Base
from .paths import SUPABASE_DIR

FILENAME = "schema.sql"
EMBEDDINGS_TABLE = "job_embeddings"

# BAAI/bge-small-en-v1.5, the model `nodes/prefilter.py` uses (DIMENSIONS there must match).
EMBEDDING_DIMENSIONS = 384

HEADER = """\
-- WERA: the Postgres schema, for Supabase.
--
-- Generated from services/agent/wera_agent/db/models.py by `uv run wera supabase`.
-- Do not edit: run the command again instead, and `uv run wera supabase --check` in CI.
--
-- To apply: paste into the Supabase SQL editor, or
--   psql "$SUPABASE_DB_URL" -f supabase/schema.sql
--
-- The agent must connect as the service role (or any role that bypasses RLS). The publishable
-- key cannot read these tables by design -- see the note on row-level security below.

create extension if not exists vector;
"""

RLS = """\

-- ---------------------------------------------------------------------------
-- Row-level security.
--
-- Supabase exposes every table in `public` over PostgREST, and the publishable (anon) key is in
-- the web app's bundle, so an exposed table is a public table. Each of these holds something
-- personal: the CV and contact details, which jobs someone looked at, what they were written.
--
-- RLS is enabled with NO policies, so `anon` and `authenticated` can read and write nothing.
-- The agent connects with the service role, which bypasses RLS. When WERA becomes multi-user
-- (ARCHITECTURE §5, phase 3), add `user_id uuid references auth.users` to each table and a
-- policy per table -- do not relax this in the meantime.
"""

EMBEDDINGS = f"""\

-- ---------------------------------------------------------------------------
-- MATCH-05: the embedding pre-filter.
--
-- Kept beside the job rather than inside `jobs.data`, because a vector column is a column.
-- `job_hash` is why a stored embedding can be trusted: a description that changed is a
-- different job, exactly as it is for the fit cache (COST-04).

create table if not exists job_embeddings (
    job_id      varchar(64) primary key references jobs (id) on delete cascade,
    embedding   vector({EMBEDDING_DIMENSIONS}) not null,
    model       varchar(128) not null,
    job_hash    varchar(32),
    created_at  varchar(32) not null
);

-- Cosine distance, which is what `prefilter.cosine` computes in Python today. `lists` suits a
-- few thousand rows; raise it if a watchlist ever grows past ~100k postings.
create index if not exists job_embeddings_vector
    on job_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);
"""


def build() -> str:
    """The whole file, as one string."""
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.dialect()
    parts = [HEADER]

    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table, if_not_exists=True).compile(dialect=dialect)).strip()
        parts.append(f"\n{ddl};\n")
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            parts.append(str(CreateIndex(index, if_not_exists=True).compile(dialect=dialect)).strip() + ";\n")

    parts.append(EMBEDDINGS)
    parts.append(RLS)
    names = [t.name for t in Base.metadata.sorted_tables] + [EMBEDDINGS_TABLE]
    parts += [f"alter table {name} enable row level security;\n" for name in names]
    # The DDL compiler ends its column lines with a trailing space.
    return "\n".join(line.rstrip() for line in "".join(parts).split("\n"))


def write(target: Path = SUPABASE_DIR) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    path = target / FILENAME
    path.write_text(build(), encoding="utf-8")
    return path


def stale(target: Path = SUPABASE_DIR) -> bool:
    path = target / FILENAME
    return not path.exists() or path.read_text(encoding="utf-8") != build()
