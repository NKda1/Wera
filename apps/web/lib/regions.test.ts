import { describe, expect, it } from "vitest";
import { boardsFor, countriesByRegion, countryName, servesCountries } from "./regions";
import type { SiteOption } from "./types";

const site = (over: Partial<SiteOption> & { domain: string }): SiteOption => ({
  name: over.domain,
  reviewed: true,
  countries: [],
  region: "europe",
  proposed: false,
  declined: false,
  examples: [],
  ...over,
});

const SITES: SiteOption[] = [
  site({ domain: "gradcracker.com", countries: ["GB"] }),
  site({ domain: "welcometothejungle.com", countries: ["FR", "NL", "GB"] }),
  site({ domain: "otta.com", countries: ["GB", "US"], reviewed: false }),
  site({ domain: "builtin.com", countries: ["US"], region: "north_america" }),
];

describe("countriesByRegion", () => {
  it("groups the countries reviewed boards cover, by region", () => {
    expect(countriesByRegion(SITES)).toEqual([
      { region: "europe", codes: ["FR", "NL", "GB"] },
      { region: "north_america", codes: ["US"] },
    ]);
  });

  it("leaves out unreviewed boards", () => {
    // SRCH-04: an unreviewed board can never be searched, so offering its countries would be
    // offering a search that cannot run. Otta is the only source of US in `europe`.
    const europe = countriesByRegion(SITES).find((g) => g.region === "europe");
    expect(europe?.codes).not.toContain("US");
  });

  it("drops a region whose boards claim no country", () => {
    expect(countriesByRegion([site({ domain: "anywhere.example" })])).toEqual([]);
  });

  it("is empty when nothing is reviewed", () => {
    expect(countriesByRegion([site({ domain: "x.example", countries: ["GB"], reviewed: false })])).toEqual([]);
  });
});

describe("servesCountries", () => {
  it("matches on any one of the chosen countries, case-insensitively", () => {
    const wttj = site({ domain: "wttj", countries: ["FR", "nl"] });
    expect(servesCountries(wttj, ["NL"])).toBe(true);
    expect(servesCountries(wttj, ["de"])).toBe(false);
  });

  it("fails open both ways, like Site.serves in the agent", () => {
    // A board with no country claim serves everywhere, and no selection rules nothing out —
    // a board vanishing from every search is indistinguishable from one that broke.
    expect(servesCountries(site({ domain: "untagged" }), ["NL"])).toBe(true);
    expect(servesCountries(site({ domain: "gb", countries: ["GB"] }), [])).toBe(true);
  });
});

describe("boardsFor", () => {
  const enabled = ["gradcracker.com", "welcometothejungle.com", "otta.com", "builtin.com"];

  it("counts only boards that are reviewed, enabled and cover a chosen country", () => {
    expect(boardsFor(SITES, ["NL"], enabled).map((s) => s.domain)).toEqual(["welcometothejungle.com"]);
  });

  it("excludes a board the person turned off even when it covers the country", () => {
    expect(boardsFor(SITES, ["GB"], ["welcometothejungle.com"]).map((s) => s.domain)).toEqual(["welcometothejungle.com"]);
  });

  it("returns every enabled reviewed board when no country is chosen", () => {
    expect(boardsFor(SITES, [], enabled).map((s) => s.domain)).toEqual([
      "gradcracker.com",
      "welcometothejungle.com",
      "builtin.com",
    ]);
  });

  it("drives the query count the dialog shows: titles x boards", () => {
    // The number must match `queries_for` in the agent, which is one query per title per board.
    const boards = boardsFor(SITES, ["GB"], enabled);
    expect(3 * boards.length).toBe(6);
  });
});

describe("countryName", () => {
  it("names a known code and passes an unknown one through", () => {
    expect(countryName("gb")).toBe("United Kingdom");
    expect(countryName("zz")).toBe("ZZ");
  });
});
