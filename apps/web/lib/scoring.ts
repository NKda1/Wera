// Scoring in code (SPEC §8). Models produce sub-scores with evidence; every total here
// is computed deterministically so changing a weight re-ranks without new LLM calls.

import type { AtsReport, FitEvidence, FitReport, FitWeights } from "./types";

export const DEFAULT_WEIGHTS: FitWeights = {
  core: 0.35,
  adjacent: 0.2,
  experience: 0.2,
  eligibility: 0.15,
  freshness: 0.1,
};

/** §8.2 rough-only jobs (no description) are capped, since the score is a title match. */
export const ROUGH_CAP = 6.5;

const DAY_MS = 24 * 60 * 60 * 1000;

export function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

/** §8.2: 10 if posted ≤ 2 days ago, 7 if ≤ 7, 4 if ≤ 14, else 1. Unknown dates score as stale. */
export function freshnessScore(postedAt: string | undefined, now: Date = new Date()): number {
  if (!postedAt) return 1;
  const days = (now.getTime() - new Date(postedAt).getTime()) / DAY_MS;
  if (days <= 2) return 10;
  if (days <= 7) return 7;
  if (days <= 14) return 4;
  return 1;
}

export function normaliseWeights(w: FitWeights): FitWeights {
  const sum = w.core + w.adjacent + w.experience + w.eligibility + w.freshness;
  if (sum <= 0) return DEFAULT_WEIGHTS;
  return {
    core: w.core / sum,
    adjacent: w.adjacent / sum,
    experience: w.experience / sum,
    eligibility: w.eligibility / sum,
    freshness: w.freshness / sum,
  };
}

export function fitTotal(
  e: Pick<FitEvidence, "core" | "adjacent" | "experience" | "eligibility">,
  freshness: number,
  weights: FitWeights,
  roughOnly: boolean,
): number {
  const w = normaliseWeights(weights);
  const total =
    w.core * e.core.score +
    w.adjacent * e.adjacent.score +
    w.experience * e.experience.score +
    w.eligibility * e.eligibility.score +
    w.freshness * freshness;
  return round1(roughOnly ? Math.min(total, ROUGH_CAP) : total);
}

export function buildFitReport(
  jobId: string,
  evidence: FitEvidence,
  postedAt: string | undefined,
  roughOnly: boolean,
  weights: FitWeights,
  now: Date = new Date(),
): FitReport {
  const freshness = freshnessScore(postedAt, now);
  return {
    ...evidence,
    jobId,
    freshness,
    roughOnly,
    total: fitTotal(evidence, freshness, weights, roughOnly),
  };
}

/** §8.3: required keywords weigh 2, nice-to-have 1. */
export function atsCoverage(keywords: Pick<AtsReport["keywords"][number], "weight" | "matched">[]): number {
  let total = 0;
  let hit = 0;
  for (const k of keywords) {
    const w = k.weight === "required" ? 2 : 1;
    total += w;
    if (k.matched) hit += w;
  }
  return total === 0 ? 0 : hit / total;
}

/** §8.3 retry rule: below threshold AND something missing that the master CV can back up. */
export function shouldRetryTailoring(report: Pick<AtsReport, "keywords" | "coverage" | "attempt">, threshold = 0.75): boolean {
  if (report.attempt >= 3) return false;
  return report.coverage < threshold && report.keywords.some((k) => !k.matched && k.inMasterCv);
}

export type ScoreBand = "strong" | "good" | "weak" | "attention";

/** Amber means you need to look: only rough, title-only matches. A low score is just weaker. */
export function scoreBand(score: number, roughOnly = false): ScoreBand {
  if (roughOnly) return "attention";
  if (score >= 8) return "strong";
  if (score >= 6.5) return "good";
  return "weak";
}
