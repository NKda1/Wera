import { describe, expect, it } from "vitest";
import type { Coverage, KeywordCategory, SkillDemand, Upskill, UpskillsReport } from "./types";
import {
  STATUSES,
  askCounts,
  filtersHref,
  focusCategory,
  focusStatus,
  importanceOf,
  missingOnly,
  mostlyPoor,
  parseFilters,
  pickView,
  scope,
  shareOf,
  toggleMissingOnly,
  toggleQuickWins,
  weighted,
} from "./upskills";

const cov = (o: Partial<Coverage> = {}): Coverage => ({ covered: 0, quickWins: 0, missing: 0, nearCovered: 0, nearQuickWins: 0, nearMissing: 0, ...o });
const row = (term: string, status: SkillDemand["status"], o: Partial<SkillDemand> = {}): SkillDemand => ({
  term,
  status,
  category: "tool",
  jobs: 1,
  nearMisses: 0,
  share: 0.1,
  importance: 1,
  nearImportance: 0,
  poor: 0,
  ...o,
});
const gap = (term: string, category: KeywordCategory = "tool"): Upskill => ({
  term,
  category,
  spellings: [term],
  jobs: 1,
  nearMisses: 0,
  share: 0.1,
  requiredShare: 1,
  score: 1,
  poor: 0,
  trend: "steady" as const,
  recent: 0,
  previous: 0,
  closeTo: [],
  examples: [],
  nearMissExamples: [],
  families: [],
  outsideField: false,
});

const report: UpskillsReport = {
  windowDays: 60,
  families: [],
  jobsConsidered: 10,
  missing: [gap("Kafka")],
  quickWins: [],
  demand: [row("Python", "covered", { jobs: 8 }), row("Kafka", "missing", { jobs: 3 })],
  coverage: cov({ covered: 8, missing: 3 }),
  byCategory: [{ category: "tool", ...cov({ covered: 8, missing: 3 }) }],
  withNearMisses: {
    nearMisses: 4,
    jobsConsidered: 10,
    nearWeight: 0.5,
    missing: [{ ...gap("Kafka"), nearMisses: 4 }, gap("Bloomberg Terminal", "tool")],
    quickWins: [],
    demand: [
      row("Python", "covered", { jobs: 8, nearMisses: 2, importance: 8, nearImportance: 1 }),
      row("Kafka", "missing", { jobs: 3, nearMisses: 4, importance: 4.5, nearImportance: 3, poor: 5 }),
      row("Bloomberg Terminal", "missing", { jobs: 0, nearMisses: 4, importance: 0, nearImportance: 2.5 }),
    ],
    coverage: cov({ covered: 8, missing: 3, nearCovered: 2, nearMissing: 8 }),
    byCategory: [{ category: "tool", ...cov({ covered: 8, missing: 3, nearCovered: 2, nearMissing: 8 }) }],
  },
};

describe("the URL", () => {
  it("reads the defaults, and a missing-only view", () => {
    expect(parseFilters({})).toEqual({ days: 60, category: "all", near: true, show: STATUSES, family: null });
    expect(parseFilters({ view: "missing" }).show).toEqual(["missing"]);
    expect(parseFilters({ view: "missing,quick_win", near: "0", days: "30", category: "tool" })).toEqual({
      days: 30,
      category: "tool",
      near: false,
      show: ["quick_win", "missing"],
      family: null,
    });
    expect(parseFilters({ view: "nonsense", days: "7", category: "x" })).toEqual({ days: 60, category: "all", near: true, show: STATUSES, family: null });
    // UPSK-07 (21 Nov): one kind of role, kept in the address; anything that isn't a family id is ignored.
    expect(parseFilters({ family: "quantitative" }).family).toBe("quantitative");
    expect(parseFilters({ family: "Robert'); drop" }).family).toBeNull();
    expect(filtersHref(parseFilters({ family: "quantitative" }))).toBe("/upskills?days=60&family=quantitative");
  });

  it("round-trips, leaving defaults out", () => {
    expect(filtersHref(parseFilters({}))).toBe("/upskills?days=60");
    const f = parseFilters({ view: "quick_win,missing", near: "0", days: "90", category: "methodology" });
    expect(filtersHref(f)).toBe("/upskills?days=90&category=methodology&near=0&view=quick_win,missing");
    expect(parseFilters(Object.fromEntries(new URLSearchParams(filtersHref(f).split("?")[1])))).toEqual(f);
  });
});

describe("the status controls", () => {
  it("'Only what I'm missing' hides covered skills, and quick wins are optional", () => {
    const on = toggleMissingOnly(STATUSES);
    expect(on).toEqual(["missing"]);
    expect(missingOnly(on)).toBe(true);
    expect(toggleQuickWins(on)).toEqual(["quick_win", "missing"]);
    expect(missingOnly(toggleQuickWins(on))).toBe(true);
    expect(toggleMissingOnly(["quick_win", "missing"])).toEqual(STATUSES);
    expect(missingOnly(STATUSES)).toBe(false);
  });

  it("a click on a status shows only it, and a second click shows everything", () => {
    expect(focusStatus(STATUSES, "covered")).toEqual(["covered"]);
    expect(focusStatus(["covered"], "covered")).toEqual(STATUSES);
    expect(focusStatus(["covered"], "missing")).toEqual(["missing"]);
    expect(focusCategory("all", "tool")).toBe("tool");
    expect(focusCategory("tool", "tool")).toBe("all");
  });
});

describe("the views", () => {
  it("switch the charts with the near misses, not only the table", () => {
    const without = pickView(report, false);
    const withNear = pickView(report, true);
    expect(without.nearMisses).toBe(0);
    expect(askCounts(without.coverage)).toEqual({ list: 11, near: 0 });
    expect(withNear.nearMisses).toBe(4);
    expect(askCounts(withNear.coverage)).toEqual({ list: 11, near: 10 });
    expect(withNear.demand.map((d) => d.term)).toContain("Bloomberg Terminal");
    // The list's side is the same in both views: near misses never change the scored counts.
    expect(askCounts(withNear.coverage).list).toBe(askCounts(without.coverage).list);
  });

  it("falls back to the scored view when no near miss was read", () => {
    expect(pickView({ ...report, withNearMisses: undefined }, true).demand).toEqual(report.demand);
  });

  it("reads a report from an agent older than its charts", () => {
    const old = { ...report, withNearMisses: { nearMisses: 4, missing: report.withNearMisses!.missing } } as unknown as UpskillsReport;
    const v = pickView(old, true);
    expect(v.demand).toEqual(report.demand);
    expect(v.quickWins).toEqual([]);
    expect(v.missing).toHaveLength(2);
    expect(v.nearMisses).toBe(0); // no near-miss chart data, so the charts don't claim any
    const bare = { covered: 2, quickWins: 1, missing: 1 } as Coverage;
    expect(askCounts(bare)).toEqual({ list: 4, near: 0 });
    expect(importanceOf({ term: "x", jobs: 3, share: 0, status: "missing" } as SkillDemand)).toBe(3);
  });

  it("weighs a near-miss ask at the view's weight", () => {
    const c = pickView(report, true).coverage;
    expect(weighted(c, "missing", 0.5)).toBe(3 + 4);
    expect(weighted(c, "covered", 0.5)).toBe(8 + 1);
    expect(shareOf(c, ["missing"], 0.5)).toBeCloseTo(7 / 16);
    expect(shareOf(c, STATUSES, 0.5)).toBeCloseTo(1);
    expect(shareOf(cov(), STATUSES, 0.5)).toBe(0);
  });
});

describe("scoping", () => {
  const view = pickView(report, true);

  it("'Only what I'm missing' scopes the charts and lists, and hides covered skills", () => {
    const s = scope(view, { category: "all", show: ["missing"] });
    expect(s.demand.map((d) => d.term)).toEqual(["Kafka", "Bloomberg Terminal"]);
    expect(s.missing.map((u) => u.term)).toEqual(["Kafka", "Bloomberg Terminal"]);
    expect(s.quickWins).toEqual([]);
    // The figures still count the whole category.
    expect(s.top).toHaveLength(3);
    expect(s.shownOnCv).toBe(1);
  });

  it("a covered-only view lists no gaps", () => {
    const s = scope(view, { category: "all", show: ["covered"] });
    expect(s.demand.map((d) => d.term)).toEqual(["Python"]);
    expect(s.missing).toEqual([]);
    expect(s.qualifications).toEqual([]);
  });

  it("a category scopes everything, and an empty one reads as zero", () => {
    expect(scope(view, { category: "methodology", show: STATUSES }).demand).toEqual([]);
    expect(askCounts(scope(view, { category: "methodology", show: STATUSES }).coverage)).toEqual({ list: 0, near: 0 });
    expect(scope(view, { category: "tool", show: STATUSES }).coverage).toBe(view.byCategory[0]);
  });

  it("'other' holds the skills the posts gave no kind", () => {
    const untyped = { ...view, demand: [row("Docker", "missing", { category: undefined })], byCategory: [{ category: "other" as const, ...cov({ missing: 1 }) }] };
    const s = scope(untyped, { category: "other", show: STATUSES });
    expect(s.demand.map((d) => d.term)).toEqual(["Docker"]);
    expect(askCounts(s.coverage).list).toBe(1);
    expect(parseFilters({ category: "other" }).category).toBe("other");
  });

  it("the biggest gap is the most important missing skill", () => {
    expect(scope(view, { category: "all", show: STATUSES }).biggestGap?.term).toBe("Kafka");
  });
});

describe("importance", () => {
  it("adds both sources, and marks a skill asked mostly by poor matches", () => {
    expect(importanceOf(report.withNearMisses!.demand[1])).toBe(7.5);
    expect(mostlyPoor({ poor: 5, jobs: 3, nearMisses: 4 })).toBe(true);
    expect(mostlyPoor({ poor: 2, jobs: 3, nearMisses: 1 })).toBe(false);
  });
});
