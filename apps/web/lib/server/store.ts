import "server-only";

// In-memory demo store (DEMO-01). It implements the behaviour behind the SPEC §9.1
// routes — state machines (§7), Gate 1 and Gate 2, intake runs — on fixtures and the
// mock writer. It lives on globalThis so route handlers and pages share one copy and
// dev reloads keep it; a server restart resets the demo. The SQLite store replaces it.

import YAML from "yaml";
import { demoContact, demoCv, demoProfile, SITES } from "../demo/cv";
import { applicationSeeds, firstRunSeeds, hiddenSeeds, shortlistSeeds, type DemoJobSeed } from "../demo/jobs";
import { slug } from "../format";
import { isLinkedInUrl } from "../urls";
import { buildFitReport, DEFAULT_WEIGHTS } from "../scoring";
import type {
  Application,
  ApplicationStatus,
  ApplicationWithJob,
  ChannelSummary,
  Contact,
  DraftStep,
  FitEvidence,
  IntakeChannel,
  IntakeRun,
  Job,
  JobStatus,
  MasterCV,
  NavCounts,
  Pack,
  ScoredJob,
  SearchProfile,
  Settings,
  SettingsResponse,
} from "../types";
import { cvLineIndex, masterCvText, textHasTerm } from "../vocab";
import { inboxFolder, inboxLabel, llmBaseUrl, llmProviderName, secretStatus } from "./env";
import { ApiError } from "./http";
import { ALLOWED } from "../applications";
import { DRAFT_STEPS, DRAFT_TOTAL_MS } from "../drafting";
import { generatePack, guardCv, refreshAts } from "./mock-llm";
import { MasterCvSchema, parse } from "./schemas";

const DAY = 24 * 60 * 60 * 1000;
const MIN = 60 * 1000;

type JobRecord = Job & { evidence: FitEvidence; questions: string[] };
type RunRecord = IntakeRun & { durationMs: number; settled: boolean };

interface Decision {
  jobId: string;
  gate: 1 | 2;
  decision: string;
  note?: string;
  decidedAt: string;
}

interface ChannelStats {
  board: { queries: number; results: number; ranAt: string };
  inbox: { emails: number; jobs: number; checkedAt: string };
  watchlist: { companies: number; newSinceYesterday: number; checkedAt: string };
}

interface State {
  cv: MasterCV;
  contact: Contact;
  profile: SearchProfile;
  settings: Settings;
  jobs: Map<string, JobRecord>;
  apps: Map<string, Application>;
  runs: Map<string, RunRecord>;
  stats: ChannelStats;
  decisions: Decision[];
  firstRunDelivered: boolean;
  lastRunJobIds: string[];
  counter: number;
}

const g = globalThis as unknown as { __weraDemo?: State };

function db(): State {
  return (g.__weraDemo ??= seed(new Date()));
}

const iso = (ms: number) => new Date(ms).toISOString();
const clone = <T>(v: T): T => structuredClone(v);

// ---------------------------------------------------------------------------
// Seeding

function toJob(s: DemoJobSeed, now: number, status: JobStatus = s.status ?? "new"): JobRecord {
  const postedAt = now - s.postedDaysAgo * DAY;
  return {
    id: s.id,
    dedupeKey: s.ats ? `ats:${s.ats.vendor}:${s.ats.boardToken}:${s.ats.jobId}` : `hash:${slug(s.company)}:${slug(s.title)}:${slug(s.city)}`,
    title: s.title,
    company: s.company,
    location: { city: s.city, country: s.country, raw: `${s.city}, ${s.country}` },
    remote: s.remote,
    salary: s.salary,
    descriptionMd: s.description,
    descriptionStatus: s.description ? "full" : "missing",
    applyUrl: s.applyUrl,
    ats: s.ats,
    linkedinUrl: s.linkedinUrl,
    sources: s.sources.map((src, i) => ({ ...src, seenAt: iso(postedAt + (i + 1) * 3 * 60 * MIN) })),
    language: "en",
    postedAt: iso(postedAt),
    firstSeen: iso(postedAt + 2 * 60 * MIN),
    lastSeen: iso(now),
    status,
    hiddenReason: s.hiddenReason,
    seniority: s.seniority,
    evidence: s.evidence,
    questions: s.questions ?? [],
  };
}

function seed(nowDate: Date): State {
  const now = nowDate.getTime();
  const ranAt = new Date(nowDate);
  ranAt.setHours(8, 2, 0, 0);
  if (ranAt.getTime() > now) ranAt.setTime(ranAt.getTime() - DAY);

  const state: State = {
    cv: clone(demoCv),
    contact: clone(demoContact),
    profile: demoProfile(nowDate),
    settings: {
      demoMode: true,
      redactContact: true,
      weeklyBudgetUsd: 5,
      spentThisWeekUsd: 1.84,
      cvPages: 1,
      baseUrl: llmBaseUrl(),
      models: { fast: "qwen/qwen3.7-flash", scoring: "qwen/qwen3.8-27b", writing: "qwen/qwen3.8-max-0902" },
      weights: { ...DEFAULT_WEIGHTS },
      inbox: {
        label: inboxLabel() ?? "[your-alerts-mailbox]",
        folder: inboxFolder(),
        lastCheckedAt: iso(now - 10 * MIN),
        emailsThisWeek: 23,
        senders: ["LinkedIn job alerts", "Indeed", "Bright Network", "Gradcracker"],
      },
    },
    jobs: new Map(),
    apps: new Map(),
    runs: new Map(),
    stats: {
      board: { queries: 20, results: 126, ranAt: ranAt.toISOString() },
      inbox: { emails: 23, jobs: 71, checkedAt: iso(now - 10 * MIN) },
      watchlist: { companies: 32, newSinceYesterday: 17, checkedAt: ranAt.toISOString() },
    },
    decisions: [],
    firstRunDelivered: false,
    lastRunJobIds: [],
    counter: 0,
  };

  for (const s of [...shortlistSeeds, ...hiddenSeeds]) state.jobs.set(s.id, toJob(s, now));

  for (const a of applicationSeeds) {
    const job = toJob(a.job, now, a.status === "rejected" ? "closed" : "in_application");
    state.jobs.set(job.id, job);
    const createdAt = now - a.daysAgo * DAY;
    const app: Application = {
      id: `app-${job.id.slice(4)}`,
      jobId: job.id,
      status: a.status,
      createdAt: iso(createdAt),
      draftStartedAt: iso(createdAt),
      notes: a.notes,
      pack: makePack(state, job),
    };
    if (a.status !== "ready_for_review") app.approvedAt = iso(createdAt + 2 * 60 * MIN);
    if (a.submittedDaysAgo != null) {
      app.submittedAt = iso(now - a.submittedDaysAgo * DAY);
      app.followUpAt = iso(a.followUpInDays != null ? now + a.followUpInDays * DAY : now - a.submittedDaysAgo * DAY + 7 * DAY);
    }
    state.apps.set(app.id, app);
  }
  return state;
}

function makePack(state: State, job: JobRecord, note?: string): Pack {
  return generatePack({ job, evidence: job.evidence, questions: job.questions, cv: state.cv, cvPages: state.settings.cvPages, note });
}

// ---------------------------------------------------------------------------
// Time-based progress: drafts and runs advance on read (no background workers in demo).

function settle(state: State, now = Date.now()) {
  for (const job of state.jobs.values()) {
    if (job.status === "snoozed" && job.snoozedUntil && new Date(job.snoozedUntil).getTime() <= now) {
      job.status = "new";
      job.snoozedUntil = undefined;
    }
  }
  for (const app of state.apps.values()) {
    if (app.status === "drafting" && app.draftStartedAt && now - new Date(app.draftStartedAt).getTime() >= DRAFT_TOTAL_MS) {
      app.status = "ready_for_review";
    }
  }
  for (const run of state.runs.values()) {
    if (run.status !== "running") continue;
    const elapsed = now - new Date(run.startedAt).getTime();
    run.progress = Math.min(1, elapsed / run.durationMs);
    if (run.channel === "board_search") run.queries = Math.round(20 * run.progress);
    if (elapsed >= run.durationMs) finishRun(state, run, now);
  }
}

function finishRun(state: State, run: RunRecord, now: number) {
  run.status = "succeeded";
  run.progress = 1;
  run.finishedAt = iso(now);
  if (run.settled) return;
  run.settled = true;

  if (run.channel === "board_search") {
    run.queries = 20;
    if (!state.firstRunDelivered) {
      state.firstRunDelivered = true;
      const added = firstRunSeeds.map((s) => toJob(s, now));
      for (const j of added) {
        j.firstSeen = iso(now);
        state.jobs.set(j.id, j);
      }
      state.lastRunJobIds = added.map((j) => j.id);
      run.results = 118;
      run.newJobs = added.length;
      run.creditsUsed = 40 + added.length * 5;
    } else {
      // SRCH-05: URLs already known are skipped, so a repeat run scrapes nothing.
      run.results = 112;
      run.newJobs = 0;
      run.creditsUsed = 40;
      state.lastRunJobIds = [];
    }
    state.stats.board = { queries: 20, results: run.results, ranAt: iso(now) };
  } else if (run.channel === "email_alert") {
    run.results = 0;
    run.newJobs = 0;
    state.stats.inbox.checkedAt = iso(now);
    state.settings.inbox.lastCheckedAt = iso(now);
  } else {
    run.queries = state.stats.watchlist.companies;
    run.results = 0;
    run.newJobs = 0;
    state.stats.watchlist.checkedAt = iso(now);
  }
}

// ---------------------------------------------------------------------------
// Reads

function scored(state: State, job: JobRecord, now = new Date()): ScoredJob {
  const { evidence, questions: _questions, ...rest } = job;
  return {
    ...rest,
    fit: buildFitReport(job.id, evidence, job.postedAt, job.descriptionStatus === "missing", state.settings.weights, now),
  };
}

export function navCounts(): NavCounts {
  const state = db();
  settle(state);
  let jobs = 0;
  let review = 0;
  for (const j of state.jobs.values()) if (j.status === "new") jobs++;
  for (const a of state.apps.values()) if (a.status === "ready_for_review") review++;
  return { jobs, review };
}

export function listJobs(opts: { status?: JobStatus; sort?: "score" | "date" } = {}): ScoredJob[] {
  const state = db();
  settle(state);
  const status = opts.status ?? "new";
  const list = [...state.jobs.values()].filter((j) => j.status === status).map((j) => scored(state, j));
  if (opts.sort === "date") list.sort((a, b) => (b.postedAt ?? "").localeCompare(a.postedAt ?? ""));
  else list.sort((a, b) => b.fit.total - a.fit.total || (b.postedAt ?? "").localeCompare(a.postedAt ?? ""));
  return list;
}

export function getJob(id: string): ScoredJob {
  const state = db();
  const job = state.jobs.get(id);
  if (!job) throw new ApiError(404, "NOT_FOUND", "That job doesn't exist.");
  return scored(state, job);
}

export function shortlistMeta() {
  const state = db();
  settle(state);
  const { board, inbox, watchlist } = state.stats;
  const p = state.profile;
  const channels: ChannelSummary[] = [
    {
      channel: "board_search",
      detail: `Firecrawl · ${SITES.filter((s) => p.sites.includes(s.domain)).map((s) => s.name).join(", ")}`,
      stats: `${board.queries} queries · ${board.results} results · ran ${hhmm(board.ranAt)}`,
      enabled: p.channels.boardSearch,
    },
    {
      channel: "email_alert",
      detail: "Read-only · LinkedIn, Indeed and board alerts",
      stats: `${inbox.emails} emails · ${inbox.jobs} jobs · checked ${minutesAgo(inbox.checkedAt)}`,
      enabled: p.channels.inbox,
    },
    {
      channel: "watchlist",
      detail: `${watchlist.companies} companies · Greenhouse, Lever, Ashby`,
      stats: `${watchlist.newSinceYesterday} new since yesterday`,
      enabled: p.channels.watchlist,
    },
  ];
  return {
    found: { total: board.results + inbox.jobs + watchlist.newSinceYesterday, board: board.results, inbox: inbox.jobs, watchlist: watchlist.newSinceYesterday },
    channels,
    newJobIds: state.lastRunJobIds,
    hidden: listJobs({ status: "hidden" }),
    snoozed: listJobs({ status: "snoozed" }).length,
    rejected: listJobs({ status: "rejected" }).length,
    activeRunIds: [...state.runs.values()].filter((r) => r.status === "running").map((r) => r.id),
  };
}

function hhmm(at: string) {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(new Date(at));
}

function minutesAgo(at: string) {
  const mins = Math.round((Date.now() - new Date(at).getTime()) / MIN);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  return `${Math.round(mins / 60)} h ago`;
}

// ---------------------------------------------------------------------------
// Gate 1 (G1-02, G1-05, MATCH-06, TRK-03)

export function decide(jobId: string, input: { action: "approve" | "snooze" | "reject" | "unhide"; days?: 1 | 3 | 7; reason?: string; force?: boolean }) {
  const state = db();
  settle(state);
  const job = state.jobs.get(jobId);
  if (!job) throw new ApiError(404, "NOT_FOUND", "That job doesn't exist.");
  const now = Date.now();

  if (input.action === "unhide") {
    if (job.status !== "hidden") throw new ApiError(409, "INVALID_STATE", "Only hidden jobs can be unhidden.");
    job.status = "new";
    job.hiddenReason = undefined;
  } else {
    if (job.status !== "new") throw new ApiError(409, "INVALID_STATE", "This job has already been decided.");
    if (input.action === "approve") {
      assertBudget(state);
      const dup = findDuplicate(state, job, now);
      if (dup && !input.force) {
        const dupJob = state.jobs.get(dup.jobId)!;
        throw new ApiError(
          409,
          "DUPLICATE_APPLICATION",
          `You already have an application for ${dupJob.title} at ${dupJob.company} (${dup.status.replace(/_/g, " ")}, started ${new Date(dup.createdAt).toLocaleDateString("en-GB", { day: "numeric", month: "short" })}).`,
        );
      }
      job.status = "in_application";
      const app: Application = {
        id: `app-${job.id.slice(4)}-${++state.counter}`,
        jobId: job.id,
        status: "drafting",
        createdAt: iso(now),
        draftStartedAt: iso(now),
        pack: makePack(state, job),
      };
      state.apps.set(app.id, app);
    } else if (input.action === "snooze") {
      const days = input.days ?? 1;
      job.status = "snoozed";
      job.snoozedUntil = iso(now + days * DAY);
    } else {
      job.status = "rejected";
    }
  }
  state.decisions.push({ jobId, gate: 1, decision: input.action, note: input.reason || (input.days ? `${input.days}d` : undefined), decidedAt: iso(now) });
  return scored(state, job);
}

function findDuplicate(state: State, job: JobRecord, now: number): Application | undefined {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  return [...state.apps.values()].find((a) => {
    if (a.status === "discarded") return false;
    const other = state.jobs.get(a.jobId);
    if (!other) return false;
    if (other.dedupeKey === job.dedupeKey) return true;
    const recent = now - new Date(a.createdAt).getTime() <= 90 * DAY;
    return recent && norm(other.company) === norm(job.company) && norm(other.title) === norm(job.title);
  });
}

function assertBudget(state: State) {
  // COST-01: every model call checks the weekly budget first.
  if (state.settings.spentThisWeekUsd >= state.settings.weeklyBudgetUsd) {
    throw new ApiError(402, "BUDGET_REACHED", `This week's model budget ($${state.settings.weeklyBudgetUsd.toFixed(2)}) is used up. Raise it in Settings to keep going.`);
  }
}

// ---------------------------------------------------------------------------
// Manual import (ATS-04)

export function importJob(input: { url: string } | { text: string }): ScoredJob {
  const state = db();
  const now = Date.now();
  const id = `job-import-${++state.counter}`;
  let seedJob: DemoJobSeed;

  if ("url" in input) {
    // Checked before any request is built, so a LinkedIn URL never leaves this function.
    if (isLinkedInUrl(input.url)) {
      throw new ApiError(422, "LINKEDIN_NOT_FETCHED", "WERA never opens LinkedIn links. Open the job on LinkedIn and paste its description instead.");
    }
    let url: URL;
    try {
      url = new URL(input.url.trim());
    } catch {
      throw new ApiError(422, "BAD_URL", "That doesn't look like a link. Paste the full address, starting with https://");
    }
    const ats = parseAtsUrl(url);
    const company = ats ? titleCase(ats.boardToken) : titleCase(url.hostname.replace(/^www\.|^careers\.|^jobs\./, "").split(".")[0]);
    seedJob = {
      id,
      title: "Graduate Platform Engineer",
      company,
      city: "London",
      country: "United Kingdom",
      remote: "hybrid",
      postedDaysAgo: 2,
      seniority: "graduate",
      description: `## ${company}
${company} is building developer tooling for teams that ship LLM features.

## The role
Python and TypeScript services, REST APIs and internal tools, working with the platform team.

## You have
- Python or TypeScript
- REST APIs and PostgreSQL
- Interest in LLMs and agents`,
      ats: ats ?? undefined,
      applyUrl: url.toString(),
      sources: [{ channel: "manual", detail: ats ? `${ats.vendor}:${ats.boardToken}` : url.hostname }],
      evidence: quickEvidence(state.cv, "Python TypeScript REST APIs PostgreSQL LLMs Agents", "Python and TypeScript services, REST APIs and internal tools"),
    };
  } else {
    const lines = input.text.split("\n").map((l) => l.trim()).filter(Boolean);
    const title = lines[0].replace(/^#+\s*/, "").slice(0, 80);
    const atLine = input.text.match(/\bat\s+([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,3})/);
    const company = atLine?.[1] ?? (lines[1] && lines[1].length < 40 ? lines[1].replace(/^#+\s*/, "") : "Unknown company");
    const quote = lines.find((l) => l.length > 40 && !l.startsWith("#"))?.slice(0, 90) ?? title;
    seedJob = {
      id,
      title,
      company,
      city: "London",
      country: "United Kingdom",
      remote: null,
      postedDaysAgo: 0,
      description: input.text,
      sources: [{ channel: "manual", detail: "pasted-text" }],
      evidence: quickEvidence(state.cv, input.text, quote),
    };
  }

  const job = toJob(seedJob, now);
  job.firstSeen = iso(now);
  const existing = [...state.jobs.values()].find((j) => j.dedupeKey === job.dedupeKey);
  if (existing) throw new ApiError(409, "ALREADY_KNOWN", `WERA already has this job: ${existing.title} at ${existing.company}.`);
  state.jobs.set(job.id, job);
  state.lastRunJobIds = [job.id];
  return scored(state, job);
}

function parseAtsUrl(url: URL): Job["ats"] | null {
  const parts = url.pathname.split("/").filter(Boolean);
  if (url.hostname.endsWith("greenhouse.io") && parts.length >= 3 && parts[1] === "jobs") {
    return { vendor: "greenhouse", boardToken: parts[0], jobId: parts[2] };
  }
  if (url.hostname === "jobs.lever.co" && parts.length >= 2) return { vendor: "lever", boardToken: parts[0], jobId: parts[1] };
  if (url.hostname === "jobs.ashbyhq.com" && parts.length >= 2) return { vendor: "ashby", boardToken: parts[0], jobId: parts[1] };
  return null;
}

const titleCase = (s: string) => s.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/** Phase 1 quick match (§8.1) for pasted jobs: keyword coverage against the CV, scored in code. */
function quickEvidence(cv: MasterCV, text: string, quote: string): FitEvidence {
  const vocab = cv.skills.map((s) => s.name).concat(["LLMs", "Agents", "Docker", "Kubernetes", "AWS", "RAG", "Model evaluation"]);
  const terms = [...new Set(vocab.filter((t) => textHasTerm(text, t)))];
  const master = masterCvText(cv);
  const backed = terms.filter((t) => textHasTerm(master, t));
  const coverage = terms.length ? backed.length / terms.length : 0.3;
  const ids = [...cv.experience, ...cv.projects]
    .flatMap((r) => r.bullets)
    .filter((b) => backed.some((t) => textHasTerm(`${b.text} ${b.tags.join(" ")}`, t)))
    .slice(0, 2)
    .map((b) => b.id);
  const core = Math.round(10 * coverage * 10) / 10;
  return {
    core: { score: core, jobQuote: quote, cvBulletIds: ids },
    adjacent: { score: Math.max(4, core - 1), jobQuote: "", cvBulletIds: [] },
    experience: { score: 6, jobQuote: "", cvBulletIds: [] },
    eligibility: { score: 7, jobQuote: "", cvBulletIds: [] },
    reason: `${backed.length} of ${terms.length || 0} key terms are backed by your CV.`,
    jobQuote: quote,
    cvBulletIds: ids,
    keywords: terms.map((term, i) => ({ term, weight: i < 5 ? ("required" as const) : ("nice" as const) })),
    model: "qwen/qwen3.7-flash",
  };
}

// ---------------------------------------------------------------------------
// Applications and Gate 2 (G2-01…05, TRK-01…02)

function draftProgress(app: Application, now = Date.now()): { step: DraftStep; progress: number } | undefined {
  if (app.status !== "drafting" || !app.draftStartedAt) return undefined;
  const elapsed = now - new Date(app.draftStartedAt).getTime();
  const step = DRAFT_STEPS.find((s) => elapsed < s.endsAtMs) ?? DRAFT_STEPS[DRAFT_STEPS.length - 1];
  return { step: step.step, progress: Math.min(1, elapsed / DRAFT_TOTAL_MS) };
}

function withJob(state: State, app: Application, includePack: boolean): ApplicationWithJob {
  const job = state.jobs.get(app.jobId)!;
  const fit = scored(state, job).fit;
  const { pack, ...rest } = app;
  return {
    ...rest,
    ...(includePack && app.status !== "drafting" ? { pack } : {}),
    job: { id: job.id, title: job.title, company: job.company, location: job.location, remote: job.remote, applyUrl: job.applyUrl, linkedinUrl: job.linkedinUrl, fitTotal: fit.total },
    draft: draftProgress(app),
  };
}

export function listApplications(): (ApplicationWithJob & { coverage?: number })[] {
  const state = db();
  settle(state);
  return [...state.apps.values()]
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    .map((a) => ({ ...withJob(state, a, false), coverage: a.status === "drafting" ? undefined : a.pack?.ats.coverage }));
}

export function getApplication(id: string): ApplicationWithJob {
  const state = db();
  settle(state);
  const app = state.apps.get(id);
  if (!app) throw new ApiError(404, "NOT_FOUND", "That application doesn't exist.");
  return withJob(state, app, true);
}

function mustApp(state: State, id: string): Application {
  const app = state.apps.get(id);
  if (!app) throw new ApiError(404, "NOT_FOUND", "That application doesn't exist.");
  return app;
}

export function regenerate(id: string, note: string) {
  const state = db();
  settle(state);
  const app = mustApp(state, id);
  if (app.status !== "ready_for_review" && app.status !== "changes_requested") {
    throw new ApiError(409, "INVALID_STATE", "Only a pack waiting for review can be regenerated.");
  }
  assertBudget(state);
  const job = state.jobs.get(app.jobId)!;
  app.status = "drafting";
  app.changeNote = note.trim() || undefined;
  app.draftStartedAt = iso(Date.now());
  app.pack = makePack(state, job, note);
  state.decisions.push({ jobId: app.jobId, gate: 2, decision: "request_changes", note, decidedAt: iso(Date.now()) });
  return withJob(state, app, true);
}

export function approvePack(id: string) {
  const state = db();
  settle(state);
  const app = mustApp(state, id);
  if (app.status !== "ready_for_review") throw new ApiError(409, "INVALID_STATE", "Only a pack waiting for review can be approved.");
  app.status = "approved";
  app.approvedAt = iso(Date.now());
  state.decisions.push({ jobId: app.jobId, gate: 2, decision: "approve", decidedAt: app.approvedAt });
  return withJob(state, app, true);
}

export function patchApplication(id: string, patch: { status?: ApplicationStatus; notes?: string; followUpAt?: string | null }) {
  const state = db();
  settle(state);
  const app = mustApp(state, id);
  const now = Date.now();
  if (patch.status && patch.status !== app.status) {
    if (!ALLOWED[app.status].includes(patch.status)) {
      throw new ApiError(409, "INVALID_TRANSITION", `An application can't move from ${app.status.replace(/_/g, " ")} to ${patch.status.replace(/_/g, " ")}.`);
    }
    const job = state.jobs.get(app.jobId)!;
    if (patch.status === "discarded") {
      // Cancelling a draft puts the job back on the shortlist; discarding a finished pack rejects it.
      job.status = app.status === "drafting" ? "new" : "rejected";
    }
    if (patch.status === "submitted") {
      app.submittedAt = iso(now);
      app.followUpAt ??= iso(now + 7 * DAY);
    }
    app.status = patch.status;
  }
  if (patch.notes !== undefined) app.notes = patch.notes;
  if (patch.followUpAt !== undefined) app.followUpAt = patch.followUpAt ?? undefined;
  return withJob(state, app, false);
}

/** G2-02 in-place edits, TAIL-06 add-back. Edits are marked and are not re-guarded. */
export function editDocument(docId: string, patch: { target: string; text?: string; bulletId?: string }) {
  const state = db();
  settle(state);
  const [appId, kind] = splitDocId(docId);
  const app = mustApp(state, appId);
  if (app.status !== "ready_for_review" || !app.pack) {
    throw new ApiError(409, "INVALID_STATE", "Only a pack waiting for review can be edited.");
  }
  const pack = app.pack;
  const job = state.jobs.get(app.jobId)!;
  const text = patch.text?.trim();

  if (kind === "cv") {
    const doc = pack.cv;
    if (patch.target === "summary") {
      if (!text) throw new ApiError(422, "VALIDATION", "The summary can't be empty.");
      doc.summary = { ...doc.summary, text, editedByUser: true };
    } else if (patch.target === "add" || patch.target === "remove") {
      if (!patch.bulletId) throw new ApiError(422, "VALIDATION", "Which line?");
      if (patch.target === "add") addBullet(state, doc, patch.bulletId);
      else removeBullet(doc, patch.bulletId);
      doc.guard = guardCv(doc, state.cv, job.evidence);
    } else {
      const m = patch.target.match(/^s(\d+)\.i(\d+)\.b(\d+)$/);
      const bullet = m && doc.sections[+m[1]]?.items[+m[2]]?.bullets[+m[3]];
      if (!bullet) throw new ApiError(404, "NOT_FOUND", "That line no longer exists.");
      if (!text) throw new ApiError(422, "VALIDATION", "A line can't be empty. Remove it instead.");
      bullet.text = text;
      bullet.editedByUser = true;
    }
    pack.ats = refreshAts(pack, { cv: state.cv, evidence: job.evidence, cvPages: state.settings.cvPages, job });
  } else if (kind === "letter") {
    const m = patch.target.match(/^p(\d+)$/);
    const para = m && pack.letter.paragraphs[+m[1]];
    if (!para) throw new ApiError(404, "NOT_FOUND", "That paragraph no longer exists.");
    if (!text) throw new ApiError(422, "VALIDATION", "A paragraph can't be empty.");
    para.text = text;
    para.editedByUser = true;
    pack.letter.wordCount = pack.letter.paragraphs.reduce((n, p) => n + p.text.split(/\s+/).filter(Boolean).length, 0);
  } else if (kind === "answers") {
    const answer = pack.answers.find((a) => a.id === patch.target);
    if (!answer) throw new ApiError(404, "NOT_FOUND", "That question no longer exists.");
    answer.answer = text || null;
  } else {
    throw new ApiError(404, "NOT_FOUND", "Unknown document.");
  }
  return withJob(state, app, true);
}

function splitDocId(docId: string): [string, string] {
  const i = docId.lastIndexOf(".");
  if (i < 0) throw new ApiError(404, "NOT_FOUND", "Unknown document.");
  return [docId.slice(0, i), docId.slice(i + 1)];
}

function addBullet(state: State, doc: Pack["cv"], bulletId: string) {
  const roles = [...state.cv.experience, ...state.cv.projects];
  const role = roles.find((r) => r.bullets.some((b) => b.id === bulletId));
  if (!role) throw new ApiError(404, "NOT_FOUND", "That line isn't in your master CV any more.");
  const bullet = role.bullets.find((b) => b.id === bulletId)!;
  const kind = state.cv.experience.includes(role) ? "experience" : "projects";
  let section = doc.sections.find((s) => s.kind === kind);
  if (!section) {
    section = { kind, items: [] };
    if (kind === "experience") doc.sections.unshift(section);
    else doc.sections.push(section);
  }
  let item = section.items.find((i) => i.roleId === role.id);
  if (!item) {
    item = { roleId: role.id, bullets: [] };
    const order = (kind === "experience" ? state.cv.experience : state.cv.projects).map((r) => r.id);
    section.items.push(item);
    section.items.sort((a, b) => order.indexOf(a.roleId) - order.indexOf(b.roleId));
  }
  item.bullets.push({ text: bullet.text, sourceIds: [bullet.id] });
  const ids = role.bullets.map((b) => b.id);
  item.bullets.sort((a, b) => ids.indexOf(a.sourceIds[0]) - ids.indexOf(b.sourceIds[0]));
  doc.omittedBulletIds = doc.omittedBulletIds.filter((id) => id !== bulletId);
}

function removeBullet(doc: Pack["cv"], bulletId: string) {
  for (const section of doc.sections) {
    for (const item of section.items) {
      const i = item.bullets.findIndex((b) => b.sourceIds[0] === bulletId);
      if (i >= 0) {
        item.bullets.splice(i, 1);
        doc.omittedBulletIds.push(bulletId);
      }
    }
    section.items = section.items.filter((it) => it.bullets.length);
  }
  doc.sections = doc.sections.filter((s) => s.items.length);
}

export function exportDocument(docId: string, format: string) {
  const state = db();
  const [appId] = splitDocId(docId);
  const app = mustApp(state, appId);
  if (!["approved", "submitted", "interview", "offer"].includes(app.status)) {
    throw new ApiError(403, "PACK_NOT_APPROVED", "Approve the pack to unlock downloads.");
  }
  if (format !== "pdf" && format !== "docx") throw new ApiError(422, "VALIDATION", "Format must be pdf or docx.");
  throw new ApiError(501, "NOT_IMPLEMENTED", "PDF and DOCX export arrives with the document renderer. Your approved pack is saved.");
}

// ---------------------------------------------------------------------------
// Intake runs (SRCH-06…08, PRF-05, UX-01)

const RUN_MS: Record<IntakeChannel, number> = { board_search: 6000, email_alert: 3500, watchlist: 2500 };

export function estimate(channels: IntakeChannel[]) {
  const state = db();
  const p = state.profile;
  const queries = channels.includes("board_search") ? p.titles.length * p.locations.length : 0;
  const pages = channels.includes("board_search") ? 10 : 0;
  return {
    queries,
    pages,
    credits: queries * 2 + pages * 5,
    usd: 0.02,
    companies: channels.includes("watchlist") ? state.stats.watchlist.companies : 0,
    demo: state.settings.demoMode,
  };
}

export function startRuns(channels: IntakeChannel[]) {
  const state = db();
  settle(state);
  if ([...state.runs.values()].some((r) => r.status === "running")) {
    throw new ApiError(409, "RUN_ACTIVE", "A search is already running. Wait for it to finish or cancel it.");
  }
  const p = state.profile.channels;
  const enabled = channels.filter((c) => (c === "board_search" ? p.boardSearch : c === "email_alert" ? p.inbox : p.watchlist));
  if (!enabled.length) throw new ApiError(422, "NO_CHANNELS", "Turn on at least one channel in your search profile.");
  assertBudget(state);
  const now = Date.now();
  const runs = enabled.map((channel) => {
    const run: RunRecord = {
      id: `run-${++state.counter}`,
      channel,
      status: "running",
      startedAt: iso(now),
      progress: 0,
      queries: 0,
      results: 0,
      newJobs: 0,
      creditsUsed: 0,
      errors: [],
      durationMs: RUN_MS[channel],
      settled: false,
    };
    state.runs.set(run.id, run);
    return run;
  });
  return { runIds: runs.map((r) => r.id) };
}

export function getRun(id: string): IntakeRun {
  const state = db();
  settle(state);
  const run = state.runs.get(id);
  if (!run) throw new ApiError(404, "NOT_FOUND", "That run doesn't exist.");
  const { durationMs: _d, settled: _s, ...rest } = run;
  return rest;
}

export function cancelRun(id: string): IntakeRun {
  const state = db();
  settle(state);
  const run = state.runs.get(id);
  if (!run) throw new ApiError(404, "NOT_FOUND", "That run doesn't exist.");
  if (run.status === "running") {
    run.status = "cancelled";
    run.finishedAt = iso(Date.now());
    run.settled = true;
  }
  return getRun(id);
}

// ---------------------------------------------------------------------------
// CV and profile (CV-01…07, PRF-01…03)

export function getCv(): MasterCV {
  return clone(db().cv);
}

export function cvLines() {
  return Object.fromEntries(cvLineIndex(db().cv));
}

export function putCv(input: unknown): { version: number } {
  const state = db();
  const cv = parse(MasterCvSchema, input) as MasterCV;
  const ids = [cv.summary?.id, ...[...cv.experience, ...cv.projects].flatMap((r) => [r.id, ...r.bullets.map((b) => b.id)]), ...cv.education.map((e) => e.id)].filter(Boolean);
  const dupes = ids.filter((id, i) => ids.indexOf(id) !== i);
  if (dupes.length) throw new ApiError(422, "VALIDATION", `Every id must be unique. Duplicated: ${[...new Set(dupes)].join(", ")}`);
  cv.version = state.cv.version + 1;
  state.cv = cv;
  return { version: cv.version };
}

export function importCvYaml(text: string): MasterCV {
  let data: unknown;
  try {
    data = YAML.parse(text);
  } catch (err) {
    throw new ApiError(422, "BAD_YAML", `That YAML doesn't parse: ${err instanceof Error ? err.message.split("\n")[0] : "syntax error"}`);
  }
  const cv = parse(MasterCvSchema, data) as MasterCV;
  cv.version = db().cv.version; // proposed only; saving increments it (CV-03)
  return cv;
}

export function getContact(): Contact {
  return clone(db().contact);
}

export function putContact(contact: Contact): Contact {
  db().contact = contact;
  return clone(contact);
}

export function getProfile(): SearchProfile {
  return clone(db().profile);
}

export function putProfile(profile: SearchProfile): SearchProfile {
  const state = db();
  state.profile = { ...profile, updatedAt: iso(Date.now()) };
  return clone(state.profile);
}

export function cvVersion(): number {
  return db().cv.version;
}

// ---------------------------------------------------------------------------
// Settings (SET-01…07)

export function getSettings(): SettingsResponse {
  const state = db();
  settle(state);
  const s = state.settings;
  return {
    ...clone(s),
    secrets: secretStatus(),
    llmProvider: llmProviderName(s.baseUrl),
  };
}

export function putSettings(patch: Partial<Omit<Settings, "inbox">> & { inbox?: { senders: string[] } }): SettingsResponse {
  const state = db();
  const { inbox, ...rest } = patch;
  Object.assign(state.settings, rest);
  if (inbox) state.settings.inbox.senders = inbox.senders;
  return getSettings();
}

export function testProvider(provider: string): { ok: boolean; code?: string; message: string } {
  const state = db();
  const secrets = secretStatus();
  if (provider !== "llm" && provider !== "firecrawl" && provider !== "inbox") {
    throw new ApiError(404, "NOT_FOUND", "Unknown provider.");
  }
  const key = provider === "llm" ? secrets.llm : provider === "firecrawl" ? secrets.firecrawl : secrets.inbox;
  if (key !== "set") {
    const name = provider === "llm" ? "LLM_API_KEY" : provider === "firecrawl" ? "FIRECRAWL_API_KEY" : "ALERTS_IMAP_APP_PASSWORD";
    return { ok: false, code: `${provider.toUpperCase()}_KEY_MISSING`, message: `${name} isn't set on the server.` };
  }
  if (state.settings.demoMode) {
    // DEMO-02: demo mode makes no outbound calls, so a live check can't run.
    return { ok: false, code: "DEMO_MODE", message: "The key is set. Demo mode makes no outbound calls, so it wasn't tested." };
  }
  return { ok: false, code: "NOT_IMPLEMENTED", message: "Live connection tests arrive with the backend." };
}
