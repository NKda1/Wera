/** Wording for the Jobs screen's filter dropdowns (one per group). Kept apart from JobsScreen so
 *  the labels and the button text can be tested without rendering it. */

/** A dropdown shows its search box only past this many options; below it, scanning is quicker. */
export const SEARCH_FROM = 8;

const capitalised = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function levelLabel(level: string): string {
  return capitalised(level.replace(/_/g, " "));
}

export function workLabel(mode: string): string {
  return mode === "onsite" ? "On-site" : capitalised(mode);
}

/** The group's name, then the first choice, then how many more: "Where · London +2". */
export function buttonText(group: string, chosen: string[]): string {
  if (!chosen.length) return group;
  return `${group} · ${chosen[0]}${chosen.length > 1 ? ` +${chosen.length - 1}` : ""}`;
}

const fold = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();

/** Case- and accent-insensitive substring, so "zur" finds Zürich. */
export function matches(label: string, query: string): boolean {
  return fold(label).includes(fold(query));
}
