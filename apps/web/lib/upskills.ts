// The Upskills page's arithmetic (20 Nov), kept out of the components so it is tested on its own.
//
// The agent sends two views of the same market: the scored jobs only, and the same with the jobs
// the CV nearly matched counted in (UPSK-06). Each carries its own charts, so the page picks a view
// and then scopes it — by category, and by which statuses the reader wants to see — without asking
// the agent again. Only the window (`?days=`) needs a new report.

import type { CategoryCoverage, Coverage, EducationDemand, QuickWin, SkillDemand, Upskill, UpskillsReport } from "./types";

export type Status = "covered" | "quick_win" | "missing";
/** One order everywhere, so the colours read the same on every chart. */
export const STATUSES: Status[] = ["covered", "quick_win", "missing"];

export const WINDOWS = [30, 60, 90];
export const CATEGORIES: { id: string; label: string }[] = [
  { id: "all", label: "All" },
  { id: "hard_skill", label: "Skills" },
  { id: "tool", label: "Tools" },
  { id: "methodology", label: "Methods" },
  { id: "qualification", label: "Qualifications" },
  // The agent's bucket for a keyword the post gave no kind (`by_category`'s "other"): a category
  // bar can be clicked to it, so the URL and the chips know it too.
  { id: "other", label: "Other" },
];
export const CATEGORY_NAMES: Record<string, string> = {
  hard_skill: "Skills",
  tool: "Tools",
  methodology: "Methods",
  qualification: "Qualifications",
  other: "Other",
};
// The most-asked skills charted. Past fifteen the bars are one or two jobs each, which is the
// table's job, not a chart's.
export const CHARTED = 15;
const NONE: Coverage = { covered: 0, quickWins: 0, missing: 0, nearCovered: 0, nearQuickWins: 0, nearMissing: 0 };

/** Everything the URL says about what the page shows. `show` is the statuses in view, in
 *  `STATUSES` order; all three by default. `?view=missing` is "Only what I'm missing". */
export type Filters = { days: number; category: string; near: boolean; show: Status[]; family: string | null };

export function parseFilters(params: { days?: string; category?: string; near?: string; view?: string; family?: string }): Filters {
  const days = WINDOWS.includes(Number(params.days)) ? Number(params.days) : 60;
  const category = CATEGORIES.some((c) => c.id === params.category) ? params.category! : "all";
  const asked = new Set((params.view ?? "").split(","));
  const show = STATUSES.filter((s) => asked.has(s));
  // UPSK-06: the near misses count unless switched off.
  // UPSK-07 (21 Nov): one kind of role ("quantitative"), filtered in the agent like the window.
  const family = params.family && /^[a-z]{2,32}$/.test(params.family) ? params.family : null;
  return { days, category, near: params.near !== "0", show: show.length ? show : STATUSES, family };
}

/** The page's address for these filters: defaults left out, so a plain link stays plain. */
export function filtersHref(f: Filters): string {
  const parts = [`days=${f.days}`];
  if (f.family) parts.push(`family=${f.family}`);
  if (f.category !== "all") parts.push(`category=${f.category}`);
  if (!f.near) parts.push("near=0");
  if (f.show.length && f.show.length < STATUSES.length) parts.push(`view=${f.show.join(",")}`);
  return `/upskills?${parts.join("&")}`;
}

const ordered = (s: Iterable<Status>): Status[] => {
  const set = new Set(s);
  return STATUSES.filter((x) => set.has(x));
};

/** "Only what I'm missing": the skills the CV doesn't show, covered ones hidden. */
export const missingOnly = (show: Status[]) => show.includes("missing") && !show.includes("covered");

/** The switch: on shows the gaps alone (quick wins too if they were already in view); off shows
 *  everything again. */
export function toggleMissingOnly(show: Status[]): Status[] {
  if (missingOnly(show)) return STATUSES;
  return show.includes("quick_win") && show.length < STATUSES.length ? ordered(["missing", "quick_win"]) : ["missing"];
}

/** "With quick wins", inside the missing-only view: the skills a CV line already shows. */
export function toggleQuickWins(show: Status[]): Status[] {
  return show.includes("quick_win") ? ordered(show.filter((s) => s !== "quick_win")) : ordered([...show, "quick_win"]);
}

/** A click on a status (a donut segment, a legend key): show only it, or everything again when it
 *  already was the only one. */
export function focusStatus(show: Status[], s: Status): Status[] {
  return show.length === 1 && show[0] === s ? STATUSES : [s];
}

/** A click on a category bar: that category, or all of them again. */
export const focusCategory = (current: string, c: string) => (current === c ? "all" : c);

/** One view of the market, the same shape whichever it is. `nearMisses` is 0 without them. */
export type View = {
  jobs: number;
  nearMisses: number;
  nearWeight: number;
  missing: Upskill[];
  quickWins: QuickWin[];
  demand: SkillDemand[];
  coverage: Coverage;
  byCategory: CategoryCoverage[];
  education?: EducationDemand | null;
};

/** A demand row with every 20 Nov field filled in: an older agent's rows are plain counts. */
const row = (d: SkillDemand): SkillDemand => ({
  ...d,
  nearMisses: d.nearMisses ?? 0,
  importance: d.importance ?? d.jobs,
  nearImportance: d.nearImportance ?? 0,
  poor: d.poor ?? 0,
});

/** The view the switch picks. An agent still running the code from before 20 Nov sends a near-miss
 *  view with only its gaps and education (seen live, 20 Nov: the page threw on its missing
 *  `quickWins`); the charts then fall back to the scored jobs' rather than breaking the page. */
export function pickView(report: UpskillsReport, near: boolean): View {
  const v = near ? report.withNearMisses : undefined;
  if (v) {
    const charted = Array.isArray(v.demand);
    return {
      jobs: v.jobsConsidered || report.jobsConsidered,
      nearMisses: charted ? v.nearMisses : 0,
      nearWeight: v.nearWeight ?? 0.5,
      missing: v.missing ?? report.missing,
      quickWins: v.quickWins ?? report.quickWins,
      demand: (charted ? v.demand : report.demand).map(row),
      coverage: v.coverage ?? report.coverage,
      byCategory: v.byCategory ?? report.byCategory,
      education: v.education,
    };
  }
  return {
    jobs: report.jobsConsidered,
    nearMisses: 0,
    nearWeight: 0.5,
    missing: report.missing,
    quickWins: report.quickWins,
    demand: report.demand.map(row),
    coverage: report.coverage,
    byCategory: report.byCategory,
    education: report.education,
  };
}

/** One status's asks in a coverage: on the list, and nearly matched. */
export function asksOf(c: Coverage, s: Status): { list: number; near: number } {
  // `?? 0`: a report from before 20 Nov has no near-miss tallies.
  if (s === "covered") return { list: c.covered ?? 0, near: c.nearCovered ?? 0 };
  if (s === "quick_win") return { list: c.quickWins ?? 0, near: c.nearQuickWins ?? 0 };
  return { list: c.missing ?? 0, near: c.nearMissing ?? 0 };
}

/** A status's asks as the charts draw them: a near-miss ask counts `weight` (0.5) of one, the
 *  weight the gap list gives a near miss. */
export const weighted = (c: Coverage, s: Status, weight: number) => {
  const a = asksOf(c, s);
  return a.list + weight * a.near;
};

export const weightedTotal = (c: Coverage, weight: number) => STATUSES.reduce((n, s) => n + weighted(c, s, weight), 0);

/** Plain counts: every ask, on the list and nearly matched. */
export function askCounts(c: Coverage): { list: number; near: number } {
  return STATUSES.reduce((t, s) => ({ list: t.list + asksOf(c, s).list, near: t.near + asksOf(c, s).near }), { list: 0, near: 0 });
}

/** The share (0–1) of what the jobs ask for that these statuses take, near misses at their weight. */
export function shareOf(c: Coverage, statuses: Status[], weight: number): number {
  const total = weightedTotal(c, weight);
  return total ? statuses.reduce((n, s) => n + weighted(c, s, weight), 0) / total : 0;
}

/** How much a demand row matters, what its bar is drawn by: each job's lift, a near miss half. */
export const importanceOf = (d: SkillDemand) => (d.importance ?? d.jobs) + (d.nearImportance ?? 0);

/** More than half the jobs asking matched the CV poorly (`upskills.POOR`, a fit of 6 or less). */
export const mostlyPoor = (x: { poor: number; jobs: number; nearMisses: number }) => x.poor * 2 > x.jobs + x.nearMisses;

/** Whether a skill is of this kind; "other" holds the skills with none, as the agent counts them. */
export function inCategory(category: string, x: { category?: string | null }): boolean {
  if (category === "all") return true;
  return category === "other" ? !x.category || x.category === "other" : x.category === category;
}

/** What the page shows for these filters: every chart, list and figure cut to the same slice. */
export function scope(view: View, f: Pick<Filters, "category" | "show">) {
  const ofKind = (x: { category?: string | null }) => inCategory(f.category, x);
  const shown = (s: Status) => f.show.includes(s);
  const asked = view.demand.filter(ofKind);
  const top = asked.slice(0, CHARTED);
  const coverage = f.category === "all" ? view.coverage : (view.byCategory.find((c) => c.category === f.category) ?? NONE);
  return {
    // The figures read the whole category, whatever the status filter: "3 of 15" needs the 15.
    top,
    demand: asked.filter((d) => shown(d.status)).slice(0, CHARTED),
    coverage,
    byCategory: view.byCategory.filter((c) => askCounts(c).list + askCounts(c).near > 0),
    missing: shown("missing") ? view.missing.filter(ofKind) : [],
    quickWins: shown("quick_win") ? view.quickWins.filter(ofKind) : [],
    // The figure counts every quick win of the kind, whatever the status filter.
    allQuickWins: view.quickWins.filter(ofKind).length,
    // What the qualifications part of the education card lists, in the category's own terms.
    qualifications: shown("missing") ? view.missing.filter((u) => u.category === "qualification") : [],
    shownOnCv: top.filter((d) => d.status !== "missing").length,
    biggestGap: asked.find((d) => d.status === "missing"),
  };
}

/** "6 jobs on your list · 14 you nearly matched", or "6 of 40 jobs" without near misses. */
export function jobsAsking(x: { jobs: number; nearMisses: number }, of: number): string {
  if (!x.nearMisses) return `${x.jobs} of ${of} job${of === 1 ? "" : "s"}`;
  return `${x.jobs} on your list · ${x.nearMisses} you nearly matched`;
}

/** UPSK-08 (22 Nov): the analyst's words for a gap row, as the table shows them. `who` sits beside
 * "Mostly asked by …" when the row has one, else on its own; `also` names the spellings merged
 * into the row ("also written as Data Build Tool"), never the row's own term. */
export function gapNotes(u: { term: string; evidence?: string | null; familyNote?: string | null; merged?: string[] }): {
  evidence: string | null;
  who: string | null;
  also: string | null;
} {
  const merged = (u.merged ?? []).filter((t, i, all) => t.toLowerCase() !== u.term.toLowerCase() && all.indexOf(t) === i);
  return {
    evidence: u.evidence?.trim() || null,
    who: u.familyNote?.trim() || null,
    also: merged.length ? `also written as ${merged.join(", ")}` : null,
  };
}
