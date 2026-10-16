import { describe, expect, it } from "vitest";
import { DEFAULT_WEIGHTS, normaliseWeights, scoreBand } from "./scoring";

// Totals are computed and tested in the agent (services/agent/tests/test_scoring.py).

describe("score bands", () => {
  it("marks only rough matches amber", () => {
    expect(scoreBand(9, true)).toBe("attention");
    expect([scoreBand(8.6), scoreBand(7.0), scoreBand(6.2)]).toEqual(["strong", "good", "weak"]);
  });
});

describe("weights preview", () => {
  it("normalises to a sum of 1 and falls back to the defaults", () => {
    const w = normaliseWeights({ core: 2, adjacent: 1, experience: 1, eligibility: 0, freshness: 0 });
    expect(w.core).toBe(0.5);
    expect(normaliseWeights({ core: 0, adjacent: 0, experience: 0, eligibility: 0, freshness: 0 })).toEqual(DEFAULT_WEIGHTS);
  });
});
