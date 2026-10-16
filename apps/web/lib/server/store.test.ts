import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./http";
import * as store from "./store";

const g = globalThis as { __weraDemo?: unknown };

beforeEach(() => {
  delete g.__weraDemo;
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const code = (fn: () => unknown) => {
  try {
    fn();
  } catch (err) {
    return err instanceof ApiError ? err.code : String(err);
  }
  return "OK";
};

describe("Gate 1", () => {
  it("lists the shortlist best-first and starts with 12 jobs and 2 packs to review", () => {
    const jobs = store.listJobs();
    expect(jobs).toHaveLength(12);
    expect(jobs.map((j) => j.fit.total).slice(0, 5)).toEqual([8.6, 8.1, 7.4, 7.0, 6.2]);
    expect(store.navCounts()).toEqual({ jobs: 12, review: 2 });
  });

  it("approving creates a drafting application that becomes ready for review (G1-05)", () => {
    vi.useFakeTimers({ now: Date.now() });
    store.decide("job-fernhill-mle", { action: "approve" });
    const app = store.listApplications().find((a) => a.jobId === "job-fernhill-mle")!;
    expect(app.status).toBe("drafting");
    vi.advanceTimersByTime(7000);
    expect(store.getApplication(app.id).status).toBe("ready_for_review");
    expect(store.navCounts()).toEqual({ jobs: 11, review: 3 });
  });

  it("snoozes, rejects, and refuses a second decision", () => {
    store.decide("job-northwind-swe", { action: "snooze", days: 3 });
    store.decide("job-kestrel-jaie", { action: "reject", reason: "Wrong location" });
    expect(store.listJobs().map((j) => j.id)).not.toContain("job-northwind-swe");
    expect(code(() => store.decide("job-kestrel-jaie", { action: "approve" }))).toBe("INVALID_STATE");
  });

  it("warns before a second application to the same company and title (TRK-03)", () => {
    expect(code(() => store.decide("job-lumen-be", { action: "approve" }))).toBe("DUPLICATE_APPLICATION");
    expect(code(() => store.decide("job-lumen-be", { action: "approve", force: true }))).toBe("OK");
  });

  it("refuses model work once the weekly budget is used (T-07)", () => {
    store.putSettings({ weeklyBudgetUsd: 0.01 });
    expect(code(() => store.decide("job-fernhill-mle", { action: "approve" }))).toBe("BUDGET_REACHED");
    expect(code(() => store.startRuns(["board_search"]))).toBe("BUDGET_REACHED");
  });

  it("re-ranks when weights change, with no new model calls", () => {
    const before = store.listJobs().map((j) => j.id);
    store.putSettings({ weights: { core: 0, adjacent: 1, experience: 0, eligibility: 0, freshness: 0 } });
    const after = store.listJobs();
    expect(after[0].id).toBe("job-northwind-swe");
    expect(after.map((j) => j.id)).not.toEqual(before);
  });
});

describe("intake", () => {
  it("never requests a LinkedIn URL on import (ATS-04, T-04)", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    expect(code(() => store.importJob({ url: "https://www.linkedin.com/jobs/view/123" }))).toBe("LINKEDIN_NOT_FETCHED");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("resolves a Greenhouse link without scraping", () => {
    const job = store.importJob({ url: "https://job-boards.greenhouse.io/acmeai/jobs/123" });
    expect(job.ats).toEqual({ vendor: "greenhouse", boardToken: "acmeai", jobId: "123" });
    expect(code(() => store.importJob({ url: "https://job-boards.greenhouse.io/acmeai/jobs/123" }))).toBe("ALREADY_KNOWN");
  });

  it("adds new jobs on the first search only, and allows one run at a time", () => {
    vi.useFakeTimers({ now: Date.now() });
    const { runIds } = store.startRuns(["board_search", "email_alert", "watchlist"]);
    expect(code(() => store.startRuns(["board_search"]))).toBe("RUN_ACTIVE");
    vi.advanceTimersByTime(7000);
    const board = runIds.map((id) => store.getRun(id)).find((r) => r.channel === "board_search")!;
    expect(board.status).toBe("succeeded");
    expect(board.newJobs).toBe(2);
    const again = store.startRuns(["board_search"]);
    vi.advanceTimersByTime(7000);
    expect(store.getRun(again.runIds[0]).newJobs).toBe(0);
  });
});

describe("Gate 2 and the tracker", () => {
  it("only lets a person approve, and only from review", () => {
    const app = store.listApplications().find((a) => a.status === "ready_for_review")!;
    expect(code(() => store.patchApplication(app.id, { status: "approved" }))).toBe("INVALID_TRANSITION");
    store.approvePack(app.id);
    expect(code(() => store.approvePack(app.id))).toBe("INVALID_STATE");
    const submitted = store.patchApplication(app.id, { status: "submitted" });
    expect(submitted.followUpAt).toBeDefined();
  });

  it("marks in-place edits and keeps the source id", () => {
    const app = store.listApplications().find((a) => a.status === "ready_for_review")!;
    const next = store.editDocument(`${app.id}.cv`, { target: "s0.i0.b0", text: "Rewritten by me." });
    const b = next.pack!.cv.sections[0].items[0].bullets[0];
    expect(b).toMatchObject({ text: "Rewritten by me.", editedByUser: true });
    expect(b.sourceIds.length).toBe(1);
  });

  it("validates CV saves and bumps the version (CV-05)", () => {
    const cv = store.getCv();
    expect(store.putCv(cv).version).toBe(cv.version + 1);
    const dupe = structuredClone(cv);
    dupe.experience[0].bullets[1].id = dupe.experience[0].bullets[0].id;
    expect(code(() => store.putCv(dupe))).toBe("VALIDATION");
  });

  it("never returns secret values from settings (SET-01)", () => {
    process.env.LLM_API_KEY = "sk-or-test-secret";
    const s = store.getSettings();
    expect(s.secrets.llm).toBe("set");
    expect(JSON.stringify(s)).not.toContain("sk-or-test-secret");
    delete process.env.LLM_API_KEY;
  });
});
