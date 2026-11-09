import "server-only";

// Client for the WERA agent service (services/agent). Route handlers forward to the agent's
// matching route; pages and the shell read through the typed helpers below. The browser
// never talks to the agent, and the agent only answers requests carrying AGENT_TOKEN.

import type {
  ApplicationList,
  ApplicationWithJob,
  Contact,
  CvLine,
  JobList,
  MasterCV,
  NavCounts,
  ScoredJob,
  SearchProfile,
  SettingsResponse,
  ShortlistMeta,
  SiteOption,
} from "../types";
import { agentConfig } from "./env";
import { ApiError } from "./http";

const DOWN = "The WERA agent service isn't running. Start WERA with `pnpm dev`.";
const SLOW = "The agent is taking too long to answer. It may still be working — reload in a moment.";

// Every request to the agent is bounded. It wasn't, and that was a hang with no error: `fetch`
// has no default timeout, so if the agent accepted the connection and then stalled, the request
// sat there until the platform killed it and the person saw a spinner that never resolved.
//
// 30s suits almost everything, because the slow work is deferred: a Gate 1 approval returns as
// soon as the decision is saved and the pack is drafted afterwards (`Store.defer`), and an
// intake run's rows exist before its response. What's left is a database read.
const DEFAULT_TIMEOUT_MS = 30_000;

// The exception, and the one route where a person waits on a model: `cv.parse` reads a whole
// uploaded document on the writing tier, synchronously, inside the request. The agent's own
// ceiling for one task is 300s (`gateway.TASK_DEADLINE_SECONDS`), so this sits above it — the
// agent should be the thing that gives up and says why, not this fetch.
const SLOW_ROUTES = [/^\/cv\/(parse|import)\b/];
const SLOW_TIMEOUT_MS = 330_000;

function timeoutFor(path: string): number {
  return SLOW_ROUTES.some((re) => re.test(path)) ? SLOW_TIMEOUT_MS : DEFAULT_TIMEOUT_MS;
}

async function call(path: string, init: { method?: string; body?: string } = {}): Promise<Response> {
  const { url, token } = agentConfig();
  if (!token) throw new ApiError(503, "AGENT_NOT_CONFIGURED", "AGENT_TOKEN isn't set on the web server. Start WERA with `pnpm dev`.");
  try {
    return await fetch(`${url}${path}`, {
      method: init.method ?? "GET",
      headers: { authorization: `Bearer ${token}`, ...(init.body !== undefined ? { "content-type": "application/json" } : {}) },
      body: init.body,
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutFor(path)),
    });
  } catch (err) {
    // A timeout is not the agent being down: it answered the connection and then didn't finish,
    // so "start WERA with pnpm dev" would be the wrong thing to tell someone. 504, not 503.
    if (err instanceof DOMException && err.name === "TimeoutError") throw new ApiError(504, "AGENT_TIMEOUT", SLOW);
    throw new ApiError(503, "AGENT_DOWN", DOWN);
  }
}

async function json<T>(path: string): Promise<T> {
  const res = await call(path);
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error;
    throw new ApiError(res.status, err?.code ?? `HTTP_${res.status}`, err?.message ?? "The agent service returned an error.", err?.fields);
  }
  return data as T;
}

/** Encodes one path segment, so an id can never reach a different agent route. */
export const seg = (value: string) => encodeURIComponent(value);

/** Route handlers: pass this request to the same-shaped agent route and return its answer as is. */
export async function forward(req: Request, path: string): Promise<Response> {
  const body = req.method === "GET" || req.method === "HEAD" ? undefined : await req.text();
  try {
    const res = await call(`${path}${new URL(req.url).search}`, { method: req.method, body: body || undefined });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json", "cache-control": "no-store" },
    });
  } catch (err) {
    if (err instanceof ApiError) return Response.json({ error: { code: err.code, message: err.message } }, { status: err.status });
    throw err;
  }
}

export const agent = {
  navCounts: () => json<NavCounts>("/nav"),
  sites: () => json<SiteOption[]>("/sites"),
  listJobs: (status = "new", sort: "score" | "date" = "score") => json<JobList>(`/jobs?status=${seg(status)}&sort=${sort}`).then((r) => r.items),
  getJob: (id: string) => json<ScoredJob>(`/jobs/${seg(id)}`),
  shortlistMeta: () => json<ShortlistMeta>("/jobs/meta"),
  listApplications: () => json<ApplicationList>("/applications").then((r) => r.items),
  getApplication: (id: string) => json<ApplicationWithJob>(`/applications/${seg(id)}`),
  getCv: () => json<MasterCV>("/cv"),
  cvLines: () => json<Record<string, CvLine>>("/cv/lines"),
  getContact: () => json<Contact>("/cv/contact"),
  getProfile: () => json<SearchProfile>("/profile"),
  getSettings: () => json<SettingsResponse>("/settings"),
};
