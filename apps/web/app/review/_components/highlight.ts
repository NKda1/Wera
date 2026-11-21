import type { AtsCompare, CompareKeyword } from "@/lib/types";

/** What a highlighted word is: on the CV, in the master CV but not this pack, or in neither. */
export type Mark = CompareKeyword["status"];

export type Segment = { text: string; mark?: Mark; term?: string };

/**
 * ATSC-08: a page cut into plain runs and highlighted keywords, from the agent's spans.
 *
 * The spans are the agent's (`nodes.ats.term_spans`), found by the matcher the ATS card counts
 * with, so nothing here decides what matches — it only lays them out. Two keywords can claim the
 * same words ("Python" inside "Python development"): the earlier start wins, then the longer, as
 * resume-matcher's longest-first highlighter does, and an overlapping span is skipped rather than
 * nested.
 */
export function segments(text: string, keywords: CompareKeyword[], side: "job" | "cv"): Segment[] {
  const spans = keywords
    .flatMap((k) => (side === "job" ? k.jobSpans : k.cvSpans).map((s) => ({ ...s, mark: k.status, term: k.term })))
    .filter((s) => s.start >= 0 && s.end <= text.length && s.start < s.end)
    .sort((a, b) => a.start - b.start || b.end - a.end);
  const out: Segment[] = [];
  let at = 0;
  for (const s of spans) {
    if (s.start < at) continue;
    if (s.start > at) out.push({ text: text.slice(at, s.start) });
    out.push({ text: text.slice(s.start, s.end), mark: s.mark, term: s.term });
    at = s.end;
  }
  if (at < text.length) out.push({ text: text.slice(at) });
  return out;
}

/** resume-matcher's colour bands for a match percentage: 80 and up, 60 and up, below. */
export function matchBand(percent: number): "good" | "fair" | "poor" {
  return percent >= 80 ? "good" : percent >= 60 ? "fair" : "poor";
}

/** The stats bar's numbers, all from the one response, so they agree with each other. */
export function compareStats(view: Pick<AtsCompare, "keywords" | "coverage" | "potentialCoverage">) {
  const count = (m: Mark) => view.keywords.filter((k) => k.status === m).length;
  return {
    total: view.keywords.length,
    matched: count("matched"),
    inCv: count("in_cv"),
    missing: count("missing"),
    percent: Math.round(view.coverage * 100),
    potential: Math.round(view.potentialCoverage * 100),
  };
}
