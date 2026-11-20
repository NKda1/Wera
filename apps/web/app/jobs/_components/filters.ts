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

/** A city as the filter compares it: trimmed, spaces collapsed, case ignored. */
export const cityKey = (city: string | null | undefined): string => (city ?? "").trim().replace(/\s+/g, " ").toLowerCase();

/** The places a shortlist offers, counted without regard to case (19 Nov).
 *
 * The agent now spells a city one way when it saves a job, but jobs saved before that carried the
 * board's own case, and the filter listed "London" and "london" as two places. Grouping here too
 * means a spelling that slips through can never split a place again. The label is the spelling
 * most jobs use (a tie goes to the one with capitals, then alphabetical); the key is what a chip's
 * id and its test use, so picking "London" also keeps the job that says "london".
 * Most common first, as the filter lists them.
 */
export function groupCities(cities: Iterable<string | null | undefined>): { key: string; label: string; count: number }[] {
  const spellings = new Map<string, Map<string, number>>();
  for (const raw of cities) {
    const key = cityKey(raw);
    if (!key) continue;
    const label = (raw as string).trim().replace(/\s+/g, " ");
    const seen = spellings.get(key) ?? new Map<string, number>();
    seen.set(label, (seen.get(label) ?? 0) + 1);
    spellings.set(key, seen);
  }
  const lowerFirst = (s: string) => Number(s === s.toLowerCase()); // "london" after "London" on a tie
  return [...spellings]
    .map(([key, seen]) => {
      const [label] = [...seen].sort((a, b) => b[1] - a[1] || lowerFirst(a[0]) - lowerFirst(b[0]) || a[0].localeCompare(b[0]))[0];
      return { key, label, count: [...seen.values()].reduce((a, b) => a + b, 0) };
    })
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}
