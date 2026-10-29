// SRCH-11: turning the boards WERA knows about into a region and country picker.
//
// The lists here are presentation only — which countries exist and which boards cover them comes
// from the agent's `/sites`, so adding a board to `config/sites.yaml` adds its country to this
// picker with no change here. A code with no name falls through as the code itself, which is ugly
// but honest, and is the signal to add it below.

import type { SiteOption } from "./types";

export const COUNTRY_NAMES: Record<string, string> = {
  GB: "United Kingdom",
  IE: "Ireland",
  NL: "Netherlands",
  DE: "Germany",
  FR: "France",
  BE: "Belgium",
  ES: "Spain",
  CH: "Switzerland",
  CZ: "Czechia",
  US: "United States",
  CA: "Canada",
  AU: "Australia",
};

export const REGION_NAMES: Record<string, string> = {
  europe: "Europe",
  north_america: "North America",
  apac: "Asia Pacific",
  global: "Global",
};

export function countryName(code: string): string {
  return COUNTRY_NAMES[code.toUpperCase()] ?? code.toUpperCase();
}

export function regionName(region: string): string {
  return REGION_NAMES[region] ?? region;
}

export type RegionGroup = { region: string; codes: string[] };

/**
 * Every country at least one *reviewed* board covers, grouped by that board's region.
 *
 * Unreviewed boards are excluded because SRCH-04 means they can never be searched — offering
 * their countries would be offering a search that cannot run.
 */
export function countriesByRegion(sites: SiteOption[]): RegionGroup[] {
  const byRegion = new Map<string, Set<string>>();
  for (const site of sites) {
    if (!site.reviewed) continue;
    const codes = byRegion.get(site.region) ?? new Set<string>();
    for (const code of site.countries) codes.add(code.toUpperCase());
    byRegion.set(site.region, codes);
  }
  return [...byRegion.entries()]
    .map(([region, codes]) => ({
      region,
      codes: [...codes].sort((a, b) => countryName(a).localeCompare(countryName(b))),
    }))
    .filter((group) => group.codes.length > 0)
    .sort((a, b) => regionName(a.region).localeCompare(regionName(b.region)));
}

/**
 * Whether a board is worth querying for a given country selection.
 *
 * Fails open in both directions, matching `Site.serves` in the agent: a board with no countries
 * serves everywhere, and an empty selection rules nothing out. A board quietly dropping out of
 * every search looks exactly like a board that broke.
 */
export function servesCountries(site: SiteOption, chosen: string[]): boolean {
  if (!chosen.length || !site.countries.length) return true;
  const wanted = new Set(chosen.map((c) => c.toUpperCase()));
  return site.countries.some((c) => wanted.has(c.toUpperCase()));
}

/** The boards a search would actually ask: reviewed, enabled, and covering a chosen country. */
export function boardsFor(sites: SiteOption[], chosen: string[], enabled: string[]): SiteOption[] {
  return sites.filter((s) => s.reviewed && enabled.includes(s.domain) && servesCountries(s, chosen));
}
