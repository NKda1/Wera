// Pack drafting steps (PERF-03). Shared by the mock writer and the progress UI.

export const DRAFT_STEPS = [
  { step: "cv", label: "Tailoring CV", endsAtMs: 2500 },
  { step: "letter", label: "Writing cover letter", endsAtMs: 4500 },
  { step: "ats", label: "Checking ATS coverage", endsAtMs: 6000 },
] as const;

export const DRAFT_TOTAL_MS = DRAFT_STEPS[DRAFT_STEPS.length - 1].endsAtMs;
