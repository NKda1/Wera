import { describe, expect, it } from "vitest";
import { displayCase, formatClosing, formatEvergreen, formatLiveness, formatOrigin, formatPostedAge, formatTime, isNew, upskillAnchor } from "./format";

describe("new jobs (SRCH-25)", () => {
  const foundBy = { runId: "run-7", channel: "board_search" as const, at: "2026-11-11T08:00:00Z" };

  it("is new until the person opens it", () => {
    expect(isNew({ foundBy })).toBe(true);
    expect(isNew({ foundBy, seenAt: "2026-11-11T09:00:00Z" })).toBe(false);
  });

  it("never calls a job from before tagging new", () => {
    expect(isNew({})).toBe(false);
  });

  // In the reader's own time zone, as the card shows it.
  const at = formatTime(foundBy.at);

  it("names where it came from and when, to the minute", () => {
    expect(formatOrigin({ foundBy })).toBe(`Search 11 Nov, ${at}`);
    expect(formatOrigin({ foundBy: { ...foundBy, channel: "email_alert" } })).toBe(`Email 11 Nov, ${at}`);
    expect(formatOrigin({ foundBy: { ...foundBy, channel: "watchlist" } })).toBe(`Employer feed 11 Nov, ${at}`);
    expect(formatOrigin({})).toBe("");
  });

  it("names the job API that found it (SRCH-31)", () => {
    expect(formatOrigin({ foundBy, via: "reed" })).toBe(`Search 11 Nov, ${at} · via Reed`);
    expect(formatOrigin({ foundBy, via: "adzuna" })).toBe(`Search 11 Nov, ${at} · via Adzuna`);
  });
});

describe("posted age", () => {
  it("says when the source gave no date (SRCH-24)", () => {
    expect(formatPostedAge({ postedPrecision: "unknown" })).toBe("Date not given");
  });
});

describe("upskill anchors (UPSK-04)", () => {
  it("are the same for the same skill however it is punctuated", () => {
    expect(upskillAnchor("CI/CD")).toBe("skill-ci-cd");
    expect(upskillAnchor("C# / .NET")).toBe("skill-c-net");
    expect(upskillAnchor("Google Cloud Platform")).toBe("skill-google-cloud-platform");
  });
});

describe("stale jobs (LIFE-02…04)", () => {
  const now = new Date("2026-11-14T12:00:00Z");
  it("says when applications close", () => {
    expect(formatClosing({ closesAt: "2026-11-14" }, now)).toBe("closes today");
    expect(formatClosing({ closesAt: "2026-11-17" }, now)).toBe("closes in 3 days");
    expect(formatClosing({ closesAt: "2026-11-10" }, now)).toBe("");
    expect(formatClosing({}, now)).toBe("");
  });
  it("says why a job is on the possibly-closed list, and flags a standing advert", () => {
    expect(formatLiveness({ liveness: "unverified", possiblyClosed: true }, now)).toBe("not confirmed open in over a week");
    expect(formatEvergreen({ evergreen: true })).toMatch(/standing advert/);
  });
});

describe("displayCase", () => {
  it.each([
    ["ELEANOR VANCE", false, "Eleanor Vance"],
    ["SEAN O'BRIEN", false, "Sean O'Brien"],
    ["NATIONAL BANK OF CANADA", true, "National Bank of Canada"],
    ["HSBC UK", true, "HSBC UK"],
    ["AECOM", true, "AECOM"],
    ["McLean Capital", true, "McLean Capital"],
  ])("%s", (raw, brand, shown) => {
    expect(displayCase(raw, { brand })).toBe(shown);
  });
});
