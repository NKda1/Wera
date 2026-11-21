// Browser-side client. It only ever calls WERA's own routes (ARCHITECTURE §7.2 #11);
// provider keys and URLs never appear here.

import type {
  ApplicationStatus,
  ApplicationWithJob,
  AtsCompare,
  Contact,
  CvFile,
  CvFileSaved,
  CvPreview,
  Estimate,
  FormatCheck,
  IntakeChannel,
  IntakeRun,
  JobsCleared,
  MasterCV,
  ProfileProposalResult,
  ScoredJob,
  SavedProfile,
  SearchProfile,
  Settings,
  SettingsResponse,
  SiteOption,
  SuggestionList,
  WritingReport,
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
/** MATCH-11: the reject dialog's chips, as the codes WERA learns from. Free text goes in `reason`. */
export type ReasonCode = "wrong_level" | "wrong_location" | "not_my_field" | "pay_too_low" | "applied_elsewhere";
export type Decision = { action: "approve" | "snooze" | "reject" | "unhide"; days?: 1 | 3 | 7; reasonCode?: ReasonCode; reason?: string; force?: boolean };

export const api = {
  decide: (jobId: string, d: Decision) => call<ScoredJob>(`/api/jobs/${jobId}/decision`, { method: "POST", body: d }),
  /** SRCH-25: the person opened this job, so its New tag goes. */
  markSeen: (jobId: string) => call<{ seen: boolean }>(`/api/jobs/${encodeURIComponent(jobId)}/seen`, { method: "POST", body: {} }),
  /** The post in a minute's reading. The first click is one fast-tier model call; the summary is kept until the post changes. */
  summariseJob: (jobId: string) => call<ScoredJob>(`/api/jobs/${encodeURIComponent(jobId)}/summary`, { method: "POST", body: {} }),
  markAllSeen: () => call<{ marked: number }>("/api/jobs/seen", { method: "POST", body: {} }),
  /** MATCH-13: a profile change the person's decisions point at — accepted, or not offered again. */
  acceptSuggestion: (id: string) => call<SearchProfile>(`/api/learning/suggestions/${encodeURIComponent(id)}/accept`, { method: "POST", body: {} }),
  dismissSuggestion: (id: string) => call<SuggestionList>(`/api/learning/suggestions/${encodeURIComponent(id)}/dismiss`, { method: "POST", body: {} }),
  /** SRCH-27: what "Clear jobs" would remove, and then removing it. A testing control. */
  clearPreview: () => call<JobsCleared>("/api/jobs/clear"),
  clearJobs: () => call<JobsCleared>("/api/jobs/clear", { method: "POST", body: {} }),
  importJob: (body: { url: string } | { text: string }) => call<ScoredJob>("/api/jobs/import", { method: "POST", body }),

  estimate: (channels: IntakeChannel[]) => call<Estimate>(`/api/intake/estimate?channels=${channels.join(",")}`),
  startRun: (channels: IntakeChannel[]) => call<{ runIds: string[] }>("/api/intake/run", { method: "POST", body: { channels } }),
  /** MATCH-05: score the next batch from the pool the broad search already found. */
  scoreMore: () => call<{ runIds: string[] }>("/api/intake/score-more", { method: "POST", body: {} }),
  /** CV-08 (18 Nov): re-score the board's jobs whose scores predate a CV change. */
  rescoreBoard: () => call<{ runIds: string[] }>("/api/intake/rescore?scope=board", { method: "POST", body: {} }),
  /** SRCH-13: check which postings are still open. Free — feeds, plain HTTP and a browser. */
  recheck: () => call<{ started: boolean }>("/api/jobs/recheck", { method: "POST", body: {} }),
  getRun: (id: string) => call<IntakeRun>(`/api/intake/runs/${id}`),
  cancelRun: (id: string) => call<IntakeRun>(`/api/intake/runs/${id}/cancel`, { method: "POST", body: {} }),

  /** Saving a changed "where" re-filters the jobs already found; `refiltered` says how many moved. */
  putProfile: (p: SearchProfile) => call<SavedProfile>("/api/profile", { method: "PUT", body: p }),
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
  patchApplication: (id: string, patch: { status?: ApplicationStatus; notes?: string; followUpAt?: string | null; reasonCode?: ReasonCode; reason?: string }) =>
    call<ApplicationWithJob>(`/api/applications/${id}`, { method: "PATCH", body: patch }),
  regenerate: (id: string, note: string) => call<ApplicationWithJob>(`/api/applications/${id}/regenerate`, { method: "POST", body: { note } }),
  approvePack: (id: string) => call<ApplicationWithJob>(`/api/applications/${id}/approve`, { method: "POST", body: {} }),
  editDocument: (docId: string, patch: { target: string; text?: string; bulletId?: string }) =>
    call<ApplicationWithJob>(`/api/documents/${docId}`, { method: "PATCH", body: patch }),
  /** What the post and the company's own About page say about it, each item quoted and checked. */
  companyBrief: (appId: string) => call<ApplicationWithJob>(`/api/applications/${appId}/company-brief`, { method: "POST", body: {} }),
  /** Redraft the letter alone, with the company brief; the pack shows as drafting until it's back. */
  rewriteLetter: (appId: string) => call<ApplicationWithJob>(`/api/applications/${appId}/letter/rewrite`, { method: "POST", body: {} }),
  /** EXP-01: the RenderCV file the CV PDF is made from, and what it prints. */
  getCvFile: (appId: string) => call<CvFile>(`/api/applications/${appId}/cv-file`),
  /** Saves the person's file, or with `reset` goes back to WERA's (keeping the design). */
  saveCvFile: (appId: string, body: { yaml: string } | { reset: true }) => call<CvFileSaved>(`/api/applications/${appId}/cv-file`, { method: "PUT", body }),
  /** The file as page images, saved or not. Problems come back as issues rather than errors. */
  previewCvFile: (appId: string, yaml: string) => call<CvPreview>(`/api/applications/${appId}/cv-file/preview`, { method: "POST", body: { yaml } }),
  // Exports are plain <a download> links to /api/documents/:id/export (EXP-01).
  /** ATSC-08: the job ad beside the CV, with each keyword's place on both. Read only. */
  getCompare: (appId: string) => call<AtsCompare>(`/api/applications/${encodeURIComponent(appId)}/compare`),
  /** ATSC-09: verbs, numbers, summary length and skills count. Suggestions only. */
  getWriting: (appId: string) => call<WritingReport>(`/api/applications/${encodeURIComponent(appId)}/writing`),
  /** ATSC-05/06: the format lint, run on the PDF as it renders now. Works before approval. */
  getFormatChecks: (docId: string) => call<{ checks: FormatCheck[] }>(`/api/documents/${docId}/checks`),

  /** An edit by default, which never deletes anything. `replace` is a new CV from an upload or
   *  import, where `fresh` starts over instead of re-scoring the jobs (18 Nov). */
  putCv: (cv: MasterCV, opts: { replace?: boolean; fresh?: boolean } = {}) =>
    call<{ version: number; clearedJobs: number }>(
      `/api/cv${opts.replace ? `?replace=true${opts.fresh === undefined ? "" : `&fresh=${opts.fresh}`}` : ""}`,
      { method: "PUT", body: cv },
    ),
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
