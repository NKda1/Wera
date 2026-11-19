import { describe, expect, it } from "vitest";
import { buttonText, levelLabel, matches, SEARCH_FROM, workLabel } from "./filters";

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
