import type { OptimiseChange, Pack } from "@/lib/types";
import { segments, type Segment } from "./highlight";

/** Where an optimiser change stands now: still on the CV, put back, or changed since by hand. */
export type ChangeState = "applied" | "undone" | "edited";

export type ChangeView = {
  change: OptimiseChange;
  state: ChangeState;
  /** The per-line edit target (`s0.i1.b2`) of the line as it reads now, when it can be undone. */
  target?: string;
  before: Segment[];
  after: Segment[];
};

/** Where `term` is in `text`, case-insensitive, with no letter or digit touching either side —
 *  the shape of the agent's matcher (`text_has_term`), enough to highlight the word it added. */
export function termSpans(text: string, term: string): { start: number; end: number }[] {
  const t = term.trim().toLowerCase();
  if (!t) return [];
  const lower = text.toLowerCase();
  const out: { start: number; end: number }[] = [];
  const word = /[\p{L}\p{N}]/u;
  for (let at = lower.indexOf(t); at !== -1; at = lower.indexOf(t, at + 1)) {
    const end = at + t.length;
    if ((at > 0 && word.test(lower[at - 1])) || (end < lower.length && word.test(lower[end]))) continue;
    out.push({ start: at, end });
  }
  return out;
}

function marked(text: string, term: string): Segment[] {
  return segments(text, [{ term, weight: "required", status: "matched", jobSpans: [], cvSpans: termSpans(text, term) }], "cv");
}

/**
 * ATSC-10: the optimiser's before-and-after, as the review rail shows it. Null when it changed
 * nothing. A line is found by its text, not by `location`: adding or removing a line since moves
 * the indices, and a line found nowhere was rewritten by hand, which nothing here should undo.
 */
export function optimiseView(pack: Pack): { from: number; to: number; changes: ChangeView[] } | null {
  const report = pack.optimise;
  if (!report || report.changes.length === 0) return null;
  const lines = pack.cv.sections.flatMap((s, si) => s.items.flatMap((item, ii) => item.bullets.map((b, bi) => ({ text: b.text, target: `s${si}.i${ii}.b${bi}` }))));
  const changes = report.changes.map((change): ChangeView => {
    const now = lines.find((l) => l.text === change.after);
    const state: ChangeState = now ? "applied" : lines.some((l) => l.text === change.before) ? "undone" : "edited";
    return { change, state, target: now?.target, before: [{ text: change.before }], after: marked(change.after, change.keyword) };
  });
  return { from: Math.round(report.scoreBefore), to: Math.round(report.scoreAfter), changes };
}
