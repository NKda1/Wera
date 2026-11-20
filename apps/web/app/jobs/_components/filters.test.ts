import { describe, expect, it } from "vitest";
import { buttonText, cityKey, groupCities, levelLabel, matches, SEARCH_FROM, workLabel } from "./filters";

describe("filter labels", () => {
  it("capitalises levels and work modes", () => {
    expect(levelLabel("graduate")).toBe("Graduate");
    expect(levelLabel("mid_senior")).toBe("Mid senior");
    expect(workLabel("remote")).toBe("Remote");
    expect(workLabel("hybrid")).toBe("Hybrid");
    expect(workLabel("onsite")).toBe("On-site");
  });
});

describe("dropdown button text", () => {
  it("is the group name until something is chosen", () => {
    expect(buttonText("Where", [])).toBe("Where");
  });

  it("names the first choice and counts the rest", () => {
    expect(buttonText("Where", ["London"])).toBe("Where · London");
    expect(buttonText("Where", ["London", "Toronto", "Zürich"])).toBe("Where · London +2");
  });
});

describe("option search", () => {
  it("only shows from eight options", () => {
    expect(SEARCH_FROM).toBe(8);
  });

  it("ignores case and accents", () => {
    expect(matches("Zürich", "zur")).toBe(true);
    expect(matches("Search 18 Nov, 23:27", "23:2")).toBe(true);
    expect(matches("London", "paris")).toBe(false);
  });
});

describe("grouping places", () => {
  it("counts London and london as one place, under the spelling most jobs use", () => {
    const cities = [...Array(312).fill("London"), "london", ...Array(10).fill("Manchester"), "manchester", "Leeds"];
    expect(groupCities(cities)).toEqual([
      { key: "london", label: "London", count: 313 },
      { key: "manchester", label: "Manchester", count: 11 },
      { key: "leeds", label: "Leeds", count: 1 },
    ]);
  });

  it("takes the most common spelling even when it is the lower-case one", () => {
    expect(groupCities(["london", "london", "London"])).toEqual([{ key: "london", label: "london", count: 3 }]);
  });

  it("settles a tie on the spelling with capitals", () => {
    expect(groupCities(["london", "LONDON", "London"])[0].label).toBe("London");
    expect(groupCities(["london", "London"])[0].label).toBe("London");
  });

  it("trims, collapses spaces and skips empties", () => {
    expect(groupCities([" New  York ", "new york", "", "   ", undefined, null])).toEqual([
      { key: "new york", label: "New York", count: 2 },
    ]);
  });

  it("keeps accents apart: only case is ignored", () => {
    expect(groupCities(["Zürich", "Zurich"]).map((p) => p.key)).toEqual(["zurich", "zürich"]);
  });

  it("gives a job's city the key its place has, whatever its case", () => {
    expect(cityKey(" LONDON ")).toBe(cityKey("London"));
    expect(cityKey(undefined)).toBe("");
  });
});
