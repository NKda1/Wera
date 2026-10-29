// Browser-side client. It only ever calls WERA's own routes (ARCHITECTURE §7.2 #11);
// provider keys and URLs never appear here.

import type {
  ApplicationStatus,
  ApplicationWithJob,
  Contact,
  Estimate,
  IntakeChannel,
  IntakeRun,
  MasterCV,
  ProfileProposalResult,
  ScoredJob,
  SearchProfile,
  Settings,
  SettingsResponse,
  SiteOption,
} from "./types";

export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public fields?: Record<string, string>,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: init?.method ?? "GET",
      headers: init?.body !== undefined ? { "content-type": "application/json" } : undefined,
      body: init?.body !== undefined ? JSON.stringify(init.body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiRequestError(0, "NETWORK", "Can't reach the WERA server. Is it still running?");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error;
    throw new ApiRequestError(res.status, err?.code ?? "HTTP_" + res.status, err?.message ?? "Something went wrong.", err?.fields);
  }
  return data as T;
}

/** A file upload. `fetch` sets the multipart boundary itself, so no content-type is set here. */
async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  let res: Response;
  try {
    res = await fetch(path, { method: "POST", body: form, cache: "no-store" });
  } catch {
    throw new ApiRequestError(0, "NETWORK", "Can't reach the WERA server. Is it still running?");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const err = data?.error;
    throw new ApiRequestError(res.status, err?.code ?? "HTTP_" + res.status, err?.message ?? "Couldn't read that file.", err?.fields);
  }
  return data as T;
}

export type { Estimate };
export type Decision = { action: "approve" | "snooze" | "reject" | "unhide"; days?: 1 | 3 | 7; reason?: string; force?: boolean };

export const api = {
  decide: (jobId: string, d: Decision) => call<ScoredJob>(`/api/jobs/${jobId}/decision`, { method: "POST", body: d }),
  importJob: (body: { url: string } | { text: string }) => call<ScoredJob>("/api/jobs/import", { method: "POST", body }),

  estimate: (channels: IntakeChannel[]) => call<Estimate>(`/api/intake/estimate?channels=${channels.join(",")}`),
  startRun: (channels: IntakeChannel[]) => call<{ runIds: string[] }>("/api/intake/run", { method: "POST", body: { channels } }),
  getRun: (id: string) => call<IntakeRun>(`/api/intake/runs/${id}`),
  cancelRun: (id: string) => call<IntakeRun>(`/api/intake/runs/${id}/cancel`, { method: "POST", body: {} }),

  putProfile: (p: SearchProfile) => call<SearchProfile>("/api/profile", { method: "PUT", body: p }),
  /** PRF-04: a proposal and its diff. Nothing is saved until putProfile. */
  generateProfile: () => call<ProfileProposalResult>("/api/profile/generate", { method: "POST", body: {} }),

  /**
   * SRCH-04/12: decide about a board WERA proposed. Returns the whole list, so the screen
   * re-renders from the server's view rather than guessing at the new state.
   */
  reviewSite: (domain: string, action: "approve" | "decline") =>
    call<SiteOption[]>(`/api/sites/${encodeURIComponent(domain)}/review`, { method: "POST", body: { action } }),

  listApplications: () => call<{ items: (ApplicationWithJob & { coverage?: number })[] }>("/api/applications"),
  getApplication: (id: string) => call<ApplicationWithJob>(`/api/applications/${id}`),
  patchApplication: (id: string, patch: { status?: ApplicationStatus; notes?: string; followUpAt?: string | null }) =>
    call<ApplicationWithJob>(`/api/applications/${id}`, { method: "PATCH", body: patch }),
  regenerate: (id: string, note: string) => call<ApplicationWithJob>(`/api/applications/${id}/regenerate`, { method: "POST", body: { note } }),
  approvePack: (id: string) => call<ApplicationWithJob>(`/api/applications/${id}/approve`, { method: "POST", body: {} }),
  editDocument: (docId: string, patch: { target: string; text?: string; bulletId?: string }) =>
    call<ApplicationWithJob>(`/api/documents/${docId}`, { method: "PATCH", body: patch }),
  // Exports are plain <a download> links to /api/documents/:id/export (EXP-01).

  putCv: (cv: MasterCV) => call<{ version: number }>("/api/cv", { method: "PUT", body: cv }),
  putContact: (c: Contact) => call<Contact>("/api/cv/contact", { method: "PUT", body: c }),
  importCv: (yaml: string) => call<MasterCV>("/api/cv/import", { method: "POST", body: { yaml } }),
  /** CV-03: a PDF, DOCX or YAML file. Returns a proposal; nothing is saved until putCv. */
  uploadCv: (file: File) => upload<MasterCV>("/api/cv/upload", file),
  /** CV text pasted in rather than uploaded — the way out when a PDF is a scan. */
  parseCv: (text: string) => call<MasterCV>("/api/cv/parse", { method: "POST", body: { text } }),

  putSettings: (patch: Partial<Omit<Settings, "inbox">> & { inbox?: { senders: string[] } }) =>
    call<SettingsResponse>("/api/settings", { method: "PUT", body: patch }),
  testProvider: (provider: "llm" | "firecrawl" | "inbox") =>
    call<{ ok: boolean; code?: string; message: string }>(`/api/settings/test/${provider}`, { method: "POST", body: {} }),
};
