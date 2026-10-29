# WERA

An open-source, agentic job-application assistant that you run yourself.

WERA reads your master CV, works out what to search for, finds roles across job boards, your
alert inbox and employers' own ATS boards, scores them against your CV with evidence you can
check, and turns the ones you approve into a tailored CV, a cover letter and an answer pack.

**A human approves every shortlist and every document.** There is no code path that submits an
application or sends a message, and every line WERA writes about you cites a bullet id from
your own CV — if it can't, the citation guard drops it.

---

## Quickstart

You need [Node 24+](https://nodejs.org) with pnpm (via `corepack`) and
[uv](https://docs.astral.sh/uv/).

```bash
git clone <your fork> wera && cd wera
corepack pnpm install
corepack pnpm dev
```

Open <http://localhost:3000>.

That's it — **no keys, no database, no accounts.** A fresh clone starts in demo mode with a
fictional CV, a shortlist, and drafted packs to look at. Demo mode makes **zero outbound
requests**: the model calls are answered by deterministic responders, the network is blocked,
and the data lives in its own `data/demo.db` so it can never mix with anything real.

`pnpm dev` starts both processes — the Python agent on `127.0.0.1:8787` and Next.js on `:3000` —
and generates a fresh `AGENT_TOKEN` that only those two share.

### Then, for real use

```bash
cp .env.example .env.local      # .env.local is git-ignored, and gitleaks blocks a key in a commit
```

Fill in what you have and set `WERA_DEMO_MODE=false`. Channels fail independently: with no
inbox configured the alert channel says so and the others still run, and a board search with a
missing or rejected key records its error on that channel's run row rather than failing the
run. The agent is the only process that reads a key; the web app never holds one.

| Variable | For |
|---|---|
| `LLM_API_KEY` | OpenRouter. **Set a per-key credit limit on it** — belt and braces with WERA's own weekly budget. |
| `FIRECRAWL_API_KEY` | Board search. The Hobby plan is enough for one run a day; see the cost model below. |
| `ALERTS_IMAP_*` | A **dedicated** mailbox for job alerts, with an app password. WERA opens it read-only and never marks anything read. |
| `DATABASE_URL` | Optional Postgres (`postgresql://…`) instead of SQLite in `data/`. Install the extra: `uv sync --extra postgres`. |
| `LOGFIRE_TOKEN` | Optional tracing. Without a token nothing is sent, whatever else is set. |

Your real data starts empty. WERA seeds a CV and a search profile to edit, but never seeds
jobs outside demo mode — a fictional vacancy on a real shortlist is indistinguishable from a
real one.

### Setting it up for you, in order

```bash
# 1. Master CV → Import → upload your CV as a PDF or DOCX. Check every section,
#    especially the dates, then save it.
# 2. Jobs → "Regenerate from CV". You see what would change and pick what to keep.
#    This is the step that decides what WERA looks for; the CV alone changes nothing.
# 3. Find employers worth watching, from the titles that profile now holds:
cd services/agent && uv run wera discover
```

Step 3 searches Greenhouse, Lever and Ashby for your titles, confirms each board against its
live feed, and writes the survivors to `config/companies.yaml` — about 8 Firecrawl credits.
After that it refreshes itself weekly, and those boards are polled **free**, every morning.
Add companies by hand whenever you like: an entry you wrote is never removed by discovery.

Then press **Search now** on the Jobs screen for the first real run.

---

## What it costs

About **$22–23 a month** for one active person: one intake run a day, 25 jobs scored a day and
ten applications a week.

| | Monthly |
|---|---:|
| Firecrawl (Hobby: 5,000 credits, ~2,700 used) | $19.00 |
| Fit scoring + keyword extraction | $2.48–3.20 |
| Application packs (~43) | $0.14–0.30 |

**Firecrawl is ~85% of it**, and model spend sits comfortably inside the $5/week default budget.
A typical intake run is about **$0.43** — $0.34 of Firecrawl credits and $0.10 of model calls.
Per application, models cost about **$0.06–0.08**, mostly the eleven jobs scored for each one
applied to rather than the writing.

Two caps keep it there, and both refuse rather than overspend: a **weekly USD budget** on model
calls (refused at 100%, warned at 80%) and a **per-run Firecrawl credit cap** counted in
Firecrawl's own units. See [ARCHITECTURE §9](<WERA system/ARCHITECTURE.md>) for the full
breakdown, and `config/wera.yaml` to change any of it.

---

## Commands

```bash
corepack pnpm dev                 # agent + web together (what you want)
corepack pnpm test                # web unit tests, then the agent suite
corepack pnpm build               # production build of the web app

cd services/agent
uv run wera serve                 # the agent on its own (needs AGENT_TOKEN set)
uv run wera migrate               # apply database migrations
uv run wera reset                 # delete this mode's database and seed it again
uv run wera schemas               # re-export the Pydantic contracts to packages/schemas/json
uv run wera supabase              # re-export the Postgres schema to supabase/schema.sql
uv run wera discover              # find ATS boards hiring for your titles (--dry-run to preview)
uv run wera eval                  # the golden eval (see below)
uv run pytest -q                  # the agent suite, network blocked
uv run ruff check . && uv run ruff format .
```

### The golden eval

Twenty job ads in `fixtures/golden/` are hand-scored once, and every candidate model is measured
against them. **Run this before changing a model id** — a scoring tier that drifts two points
high re-orders every morning's shortlist and nothing in the product would tell you.

```bash
uv run wera eval                                   # demo responders: proves the harness runs, costs nothing
uv run wera eval --model qwen/qwen3.8-max-0902     # live, against a real key
uv run wera eval --strict                          # exit non-zero below 80% agreement
```

It reports agreement within ±1.5 of your hand scores, keyword recall, and what the run cost,
and writes a markdown and JSON report to `data/eval/`. Every run uses a throwaway database, so
it never touches your real shortlist — which also means a live eval's spend is not added to the
weekly budget, so the report prints the cost for you to read.

Hand scores start empty. Fill in `fixtures/golden/hand_scores.yaml` (one number per ad, 0–10,
scored against the CV in `fixtures/demo/cv.json`) and the agreement figure appears. Re-score if
you change that CV: a hand score is a judgement about one CV against one ad.

---

## How it's built

```
Browser → Next.js route handlers (BFF) → FastAPI + LangGraph → LangChain → OpenRouter → Pydantic
```

- **Python owns the data and the logic.** The Next.js route handlers forward to the agent over
  loopback and hold no provider keys. Document rendering (PDF, DOCX) is the one piece of logic
  still in TypeScript, because that's where the libraries are.
- **Pydantic is the canonical schema.** JSON Schema is exported from it and the TypeScript types
  are generated from that. CI fails if either is stale.
- **One way out to a model.** `llm/gateway.py:structured()` is the only function that reaches a
  provider, so the budget check, the contact redaction, the untrusted-data wrapping, the
  validation and the cost accounting can't be skipped.
- **The gates are LangGraph `interrupt()` calls** against a checkpointer, so a decision waiting
  on you is still waiting after a restart.
- **The cheap checks run first:** hard filters, then local embeddings, then a model. Against 15
  real ATS boards carrying 3,913 live vacancies, the free filters left 88 — the single biggest
  reason being "not one of your target titles". Nothing reached a model until then.
- **The watchlist maintains itself.** Discovery is paid and weekly; watching is free and daily.

`WERA system/ARCHITECTURE.md` is how it's built; `WERA system/SPEC.md` is what it must do, with
the requirement ids the code comments refer to.

---

## Where your data goes

- **CVs and job-search activity are personal data**, and WERA treats them that way.
- Contact details live apart from the CV and are **redacted before any prompt is built**; they
  reappear only in the rendered PDF and DOCX.
- Model calls are routed with `data_collection: "deny"`, so no provider that trains on prompts
  is used.
- `llm_calls` records **metadata only** — task, model, token counts, cost, latency. No prompt
  text, no completion text, no provider error bodies.
- **WERA never touches linkedin.com.** LinkedIn jobs arrive only through alert emails you
  already receive; their URLs are stored and never fetched (User Agreement §8.2).
- A site is searched only after its terms have been reviewed and recorded in `config/sites.yaml`.

See [SECURITY.md](SECURITY.md) to report a leak or a vulnerability.

---

## Licence

MIT — see [LICENSE](LICENSE).
