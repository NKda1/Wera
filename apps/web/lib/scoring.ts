// Scoring helpers the UI needs. Totals themselves are computed by the agent (SPEC §8.2);
// the settings screen only previews how weights normalise.

import type { FitWeights } from "./types";

export const DEFAULT_WEIGHTS: FitWeights = {
  core: 0.35,
  adjacent: 0.2,
  experience: 0.2,
  eligibility: 0.15,
  freshness: 0.1,
};

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

export type ScoreBand = "strong" | "good" | "weak" | "attention";

/** Amber means you need to look: only rough, title-only matches. A low score is just weaker. */
export function scoreBand(score: number, roughOnly = false): ScoreBand {
  if (roughOnly) return "attention";
  if (score >= 8) return "strong";
  if (score >= 6.5) return "good";
  return "weak";
}
