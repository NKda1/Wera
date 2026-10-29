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

CREATE TABLE IF NOT EXISTS contact (
	id SERIAL NOT NULL,
	email VARCHAR(320) NOT NULL,
	phone VARCHAR(64),
	address TEXT,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS crawl_cache (
	key VARCHAR(80) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	payload JSONB NOT NULL,
	stored_at VARCHAR(32) NOT NULL,
	expires_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (key)
);
CREATE INDEX IF NOT EXISTS ix_crawl_cache_expires_at ON crawl_cache (expires_at);
CREATE INDEX IF NOT EXISTS ix_crawl_cache_kind ON crawl_cache (kind);

CREATE TABLE IF NOT EXISTS decisions (
	id SERIAL NOT NULL,
	job_id VARCHAR(64) NOT NULL,
	gate INTEGER NOT NULL,
	decision VARCHAR(32) NOT NULL,
	note TEXT,
	decided_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_decisions_job_id ON decisions (job_id);

CREATE TABLE IF NOT EXISTS intake_runs (
	id VARCHAR(64) NOT NULL,
	channel VARCHAR(32) NOT NULL,
	status VARCHAR(16) NOT NULL,
	started_at VARCHAR(32) NOT NULL,
	finished_at VARCHAR(32),
	progress FLOAT NOT NULL,
	phase VARCHAR(16) NOT NULL,
	done INTEGER NOT NULL,
	total INTEGER NOT NULL,
	queries INTEGER NOT NULL,
	results INTEGER NOT NULL,
	new_jobs INTEGER NOT NULL,
	credits_used INTEGER NOT NULL,
	credits_saved INTEGER NOT NULL,
	errors JSONB NOT NULL,
	duration_ms INTEGER NOT NULL,
	settled BOOLEAN NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_intake_runs_status ON intake_runs (status);

CREATE TABLE IF NOT EXISTS jobs (
	id VARCHAR(64) NOT NULL,
	dedupe_key VARCHAR(512) NOT NULL,
	status VARCHAR(32) NOT NULL,
	title TEXT NOT NULL,
	company TEXT NOT NULL,
	posted_at VARCHAR(32),
	first_seen VARCHAR(32) NOT NULL,
	snoozed_until VARCHAR(32),
	data JSONB NOT NULL,
	questions JSONB NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (dedupe_key)
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status);

CREATE TABLE IF NOT EXISTS kv (
	key VARCHAR(64) NOT NULL,
	value JSONB NOT NULL,
	PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS llm_calls (
	id SERIAL NOT NULL,
	task VARCHAR(32) NOT NULL,
	tier VARCHAR(16) NOT NULL,
	model VARCHAR(128) NOT NULL,
	input_tokens INTEGER NOT NULL,
	output_tokens INTEGER NOT NULL,
	cost_usd FLOAT NOT NULL,
	latency_ms INTEGER NOT NULL,
	ok BOOLEAN NOT NULL,
	code VARCHAR(32),
	attempt INTEGER NOT NULL,
	called_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_llm_calls_called_at ON llm_calls (called_at);
CREATE INDEX IF NOT EXISTS ix_llm_calls_task ON llm_calls (task);

CREATE TABLE IF NOT EXISTS master_cv (
	version INTEGER NOT NULL,
	data JSONB NOT NULL,
	saved_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (version)
);

CREATE TABLE IF NOT EXISTS search_profile (
	id SERIAL NOT NULL,
	data JSONB NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS settings (
	id SERIAL NOT NULL,
	data JSONB NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS applications (
	id VARCHAR(80) NOT NULL,
	job_id VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	draft_started_at VARCHAR(32),
	draft_step VARCHAR(16),
	change_note TEXT,
	approved_at VARCHAR(32),
	submitted_at VARCHAR(32),
	follow_up_at VARCHAR(32),
	notes TEXT,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX IF NOT EXISTS ix_applications_job_id ON applications (job_id);
CREATE INDEX IF NOT EXISTS ix_applications_status ON applications (status);

CREATE TABLE IF NOT EXISTS fit_reports (
	job_id VARCHAR(64) NOT NULL,
	evidence JSONB NOT NULL,
	model VARCHAR(128) NOT NULL,
	cv_version INTEGER,
	job_hash VARCHAR(32),
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (job_id),
	FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS documents (
	id SERIAL NOT NULL,
	application_id VARCHAR(80) NOT NULL,
	kind VARCHAR(16) NOT NULL,
	version INTEGER NOT NULL,
	content JSONB NOT NULL,
	created_at VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (application_id, kind, version),
	FOREIGN KEY(application_id) REFERENCES applications (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_documents_application_id ON documents (application_id);

-- ---------------------------------------------------------------------------
-- MATCH-05: the embedding pre-filter.
--
-- Kept beside the job rather than inside `jobs.data`, because a vector column is a column.
-- `job_hash` is why a stored embedding can be trusted: a description that changed is a
-- different job, exactly as it is for the fit cache (COST-04).

create table if not exists job_embeddings (
    job_id      varchar(64) primary key references jobs (id) on delete cascade,
    embedding   vector(384) not null,
    model       varchar(128) not null,
    job_hash    varchar(32),
    created_at  varchar(32) not null
);

-- Cosine distance, which is what `prefilter.cosine` computes in Python today. `lists` suits a
-- few thousand rows; raise it if a watchlist ever grows past ~100k postings.
create index if not exists job_embeddings_vector
    on job_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);

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
alter table contact enable row level security;
alter table crawl_cache enable row level security;
alter table decisions enable row level security;
alter table intake_runs enable row level security;
alter table jobs enable row level security;
alter table kv enable row level security;
alter table llm_calls enable row level security;
alter table master_cv enable row level security;
alter table search_profile enable row level security;
alter table settings enable row level security;
alter table applications enable row level security;
alter table fit_reports enable row level security;
alter table documents enable row level security;
alter table job_embeddings enable row level security;
