import { describe, expect, it } from "vitest";
import type { CompareKeyword } from "@/lib/types";
import { compareStats, matchBand, segments } from "./highlight";

const kw = (term: string, status: CompareKeyword["status"], jobSpans: [number, number][], cvSpans: [number, number][] = []): CompareKeyword => ({
  term,
  status,
  weight: "required",
  jobSpans: jobSpans.map(([start, end]) => ({ start, end })),
  cvSpans: cvSpans.map(([start, end]) => ({ start, end })),
});

describe("segments", () => {
  const text = "Python development and SQL, plus C#.";

  it("cuts the page into plain runs and marked keywords, in order", () => {
    const out = segments(text, [kw("SQL", "matched", [[23, 26]]), kw("C#", "missing", [[33, 35]])], "job");
    expect(out.map((s) => s.text).join("")).toBe(text);
    expect(out.filter((s) => s.mark).map((s) => [s.text, s.mark])).toEqual([
      ["SQL", "matched"],
      ["C#", "missing"],
    ]);
  });

  it("gives overlapping keywords to the earlier, then the longer, and never nests them", () => {
    const out = segments(text, [kw("Python", "matched", [[0, 6]]), kw("Python development", "in_cv", [[0, 18]])], "job");
    expect(out[0]).toEqual({ text: "Python development", mark: "in_cv", term: "Python development" });
    expect(out.map((s) => s.text).join("")).toBe(text);
  });

  it("reads the side asked for, and ignores spans outside the text", () => {
    const k = kw("SQL", "matched", [[23, 26]], [[0, 3], [90, 99]]);
    expect(segments("SQL only", [k], "cv")).toEqual([{ text: "SQL", mark: "matched", term: "SQL" }, { text: " only" }]);
  });

  it("returns the page whole when nothing matches", () => {
    expect(segments(text, [], "cv")).toEqual([{ text }]);
  });
});

describe("matchBand", () => {
  it("uses resume-matcher's 80 and 60", () => {
    expect([matchBand(80), matchBand(79), matchBand(60), matchBand(59)]).toEqual(["good", "fair", "fair", "poor"]);
  });
});

describe("compareStats", () => {
  it("counts each status and rounds the weighted coverage", () => {
    const view = { keywords: [kw("A", "matched", []), kw("B", "in_cv", []), kw("C", "missing", []), kw("D", "missing", [])], coverage: 0.333, potentialCoverage: 0.5 };
    expect(compareStats(view)).toEqual({ total: 4, matched: 1, inCv: 1, missing: 2, percent: 33, potential: 50 });
  });
});
