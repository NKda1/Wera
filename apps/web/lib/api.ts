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
  ScoredJob,
  SearchProfile,
  Settings,
  SettingsResponse,
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

  putSettings: (patch: Partial<Omit<Settings, "inbox">> & { inbox?: { senders: string[] } }) =>
    call<SettingsResponse>("/api/settings", { method: "PUT", body: patch }),
  testProvider: (provider: "llm" | "firecrawl" | "inbox") =>
    call<{ ok: boolean; code?: string; message: string }>(`/api/settings/test/${provider}`, { method: "POST", body: {} }),
};
