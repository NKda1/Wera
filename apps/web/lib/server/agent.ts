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

async function call(path: string, init: { method?: string; body?: string } = {}): Promise<Response> {
  const { url, token } = agentConfig();
  if (!token) throw new ApiError(503, "AGENT_NOT_CONFIGURED", "AGENT_TOKEN isn't set on the web server. Start WERA with `pnpm dev`.");
  try {
    return await fetch(`${url}${path}`, {
      method: init.method ?? "GET",
      headers: { authorization: `Bearer ${token}`, ...(init.body !== undefined ? { "content-type": "application/json" } : {}) },
      body: init.body,
      cache: "no-store",
    });
  } catch {
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
