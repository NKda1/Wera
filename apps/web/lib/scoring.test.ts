import { describe, expect, it } from "vitest";
import { shortlistSeeds } from "./demo/jobs";
import { atsCoverage, DEFAULT_WEIGHTS, fitTotal, freshnessScore, ROUGH_CAP, shouldRetryTailoring } from "./scoring";

const now = new Date("2026-10-12T09:00:00Z");
const daysAgo = (d: number) => new Date(now.getTime() - d * 864e5).toISOString();

describe("freshness (§8.2)", () => {
  it("uses the documented bands", () => {
    expect(freshnessScore(daysAgo(1), now)).toBe(10);
    expect(freshnessScore(daysAgo(2), now)).toBe(10);
    expect(freshnessScore(daysAgo(5), now)).toBe(7);
    expect(freshnessScore(daysAgo(10), now)).toBe(4);
    expect(freshnessScore(daysAgo(30), now)).toBe(1);
    expect(freshnessScore(undefined, now)).toBe(1);
  });
});

describe("fit total (§8.2)", () => {
  it("reproduces the mockup's top five from fixture sub-scores", () => {
    const totals = shortlistSeeds.slice(0, 5).map((s) => {
      const fresh = freshnessScore(daysAgo(s.postedDaysAgo), now);
      return fitTotal(s.evidence, fresh, DEFAULT_WEIGHTS, s.description === null);
    });
    expect(totals).toEqual([8.6, 8.1, 7.4, 7.0, 6.2]);
  });

  it("caps rough, title-only matches", () => {
    const e = { core: { score: 10 }, adjacent: { score: 10 }, experience: { score: 10 }, eligibility: { score: 10 } } as never;
    expect(fitTotal(e, 10, DEFAULT_WEIGHTS, true)).toBe(ROUGH_CAP);
  });

  it("re-ranks from weights alone, without new model output", () => {
    const [fernhill, northwind] = shortlistSeeds;
    const adjacentHeavy = { core: 0.1, adjacent: 0.6, experience: 0.1, eligibility: 0.1, freshness: 0.1 };
    const a = fitTotal(fernhill.evidence, 10, adjacentHeavy, false);
    const b = fitTotal(northwind.evidence, 10, adjacentHeavy, false);
    expect(b).toBeGreaterThan(a);
  });
});

describe("ATS coverage (§8.3)", () => {
  it("weighs required keywords double", () => {
    const required = (matched: boolean) => ({ weight: "required" as const, matched });
    const nice = (matched: boolean) => ({ weight: "nice" as const, matched });
    // 7 required (one missing) + 7 nice (one missing) = 18 / 21
    const kws = [...Array(6).fill(required(true)), required(false), ...Array(6).fill(nice(true)), nice(false)];
    expect(Math.round(atsCoverage(kws) * 100)).toBe(86);
  });

  it("retries only when a missing keyword is backed by the master CV", () => {
    const base = { coverage: 0.5, attempt: 1 };
    expect(shouldRetryTailoring({ ...base, keywords: [{ term: "Docker", weight: "required", matched: false, inMasterCv: false }] })).toBe(false);
    expect(shouldRetryTailoring({ ...base, keywords: [{ term: "Python", weight: "required", matched: false, inMasterCv: true }] })).toBe(true);
    expect(shouldRetryTailoring({ ...base, attempt: 3, keywords: [{ term: "Python", weight: "required", matched: false, inMasterCv: true }] })).toBe(false);
  });
});
