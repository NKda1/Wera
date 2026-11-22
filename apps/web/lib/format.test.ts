import { describe, expect, it } from "vitest";
import { attribution, displayCase, employerLine, formatClosing, formatEvergreen, formatLiveness, formatOrigin, formatPostedAge, formatTime, isNew, allowanceParts, previewLabel, previewsLeft, previewSource, sortPreviews, upskillAnchor } from "./format";

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
    expect(formatOrigin({ foundBy, via: "himalayas" })).toBe(`Search 11 Nov, ${at} · via Himalayas`);
    expect(formatOrigin({ foundBy, via: "arbeitnow" })).toBe(`Search 11 Nov, ${at} · via Arbeitnow`);
    expect(formatOrigin({ foundBy, via: "themuse" })).toBe(`Search 11 Nov, ${at} · via The Muse`);
    expect(formatOrigin({ foundBy, via: "jooble" })).toBe(`Search 11 Nov, ${at} · via Jooble`);
  });

  // Their terms: a visible link to the job on Himalayas (or himalayas.app), and Adzuna's name linked.
  it("credits Himalayas with a link to the job's page there, and Adzuna with its home page", () => {
    const page = "https://himalayas.app/companies/acme/jobs/junior-analyst";
    expect(attribution({ via: "himalayas", viaUrl: page })).toEqual({ label: "Jobs by Himalayas", href: page });
    expect(attribution({ via: "himalayas" })).toEqual({ label: "Jobs by Himalayas", href: "https://himalayas.app" });
    expect(attribution({ via: "adzuna" })).toEqual({ label: "Jobs by Adzuna", href: "https://www.adzuna.co.uk" });
    expect(attribution({ via: "reed" })).toBeNull();
    // 22 Nov: no attribution rule found for these three (their terms aren't reviewed yet).
    expect(attribution({ via: "jooble" })).toBeNull();
    expect(attribution({ via: "themuse" })).toBeNull();
    expect(attribution({})).toBeNull();
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

describe("previews (21 Nov)", () => {
  it("labels a match by the agent's cut-offs, never as a number", () => {
    const cut = { strong: 0.8, good: 0.72 };
    expect(previewLabel(0.91, cut)).toEqual({ id: "strong", label: "Strong match" });
    expect(previewLabel(0.8, cut).id).toBe("strong");
    expect(previewLabel(0.75, cut).id).toBe("good");
    expect(previewLabel(0.5, cut).id).toBe("possible");
    expect(previewLabel(undefined, cut).id).toBe("possible");
    expect(previewLabel(0.75, { strong: 0.7, good: 0.6 }).id).toBe("strong");
  });

  it("names the employer and the job API that found it", () => {
    expect(previewSource({ company: "Larkspur Payments", via: "adzuna" })).toBe("Larkspur Payments · via Adzuna");
    expect(previewSource({ company: "Citi", postedBy: "eFinancialCareers", via: "careerjet" })).toBe("Citi (posted by eFinancialCareers) · via Careerjet");
    expect(previewSource({ company: "eFinancialCareers", via: "reed", employerNamed: false })).toBe("Employer not named (posted by eFinancialCareers) · via Reed");
  });

  // 22 Nov: a link the hunter found names the site it was on, and an employer only when something
  // named one — its address (a Greenhouse tenant), its text, or an aggregator's title.
  it("says where a hunted link was found, and never a board as the employer", () => {
    const foundOn = { site: "Totaljobs", kind: "posting" as const, employerKnown: false };
    expect(previewSource({ company: "Totaljobs", foundOn, employerNamed: false })).toBe("Found on Totaljobs");
    expect(previewSource({ company: "Acmerobotics", foundOn: { ...foundOn, site: "Built In London", employerKnown: true }, employerNamed: true })).toBe(
      "Acmerobotics · found on Built In London",
    );
    expect(previewSource({ company: "Acme Robotics", foundOn: { site: "beBee", kind: "copy", employerKnown: true } })).toBe("Acme Robotics · found on beBee");
  });

  it("orders best match first, then newest", () => {
    const jobs = [
      { id: "a", previewMatch: 0.7, postedAt: "2026-11-20" },
      { id: "b", previewMatch: 0.9, postedAt: "2026-11-01" },
      { id: "c", previewMatch: 0.7, postedAt: "2026-11-21" },
      { id: "d", postedAt: "2026-11-22" },
    ];
    expect(sortPreviews(jobs).map((j) => j.id)).toEqual(["b", "c", "a", "d"]);
  });
});

describe("previewsLeft (21 Nov)", () => {
  const listed = [{ id: "a" }, { id: "b" }, { id: "c" }];
  it("takes dismissed previews off the agent's total", () => {
    expect(previewsLeft(3, listed, new Set())).toBe(3);
    expect(previewsLeft(3, listed, new Set(["a"]))).toBe(2);
    expect(previewsLeft(120, listed, new Set(["a", "b"]))).toBe(118);
  });
  it("does not take one off twice once the page has refreshed without it", () => {
    // `a` was read, the refresh dropped it from both the list and the total.
    expect(previewsLeft(119, [{ id: "b" }, { id: "c" }], new Set(["a"]))).toBe(119);
    expect(previewsLeft(2, [{ id: "b" }, { id: "c" }], new Set(["a"]))).toBe(2);
  });
  it("never goes below zero", () => {
    expect(previewsLeft(1, listed, new Set(["a", "b", "c"]))).toBe(0);
  });
});

describe("allowanceParts (H4, 21 Nov)", () => {
  it("says what is left of both allowances", () => {
    expect(allowanceParts({ googleSearchesLeftToday: 41, googleSearchesPerDay: 80, weeklyCreditCap: 500, creditsThisWeek: 120 })).toEqual([
      "Google searches left today: 41 of 80",
      "Firecrawl credits left this week: 380 of 500",
    ]);
  });
  it("leaves out a limit it isn't told, and never shows a negative", () => {
    expect(allowanceParts({})).toEqual([]);
    expect(allowanceParts({ googleSearchesLeftToday: 3, googleSearchesPerDay: null })).toEqual([]);
    expect(allowanceParts({ weeklyCreditCap: 100, creditsThisWeek: 130 })).toEqual(["Firecrawl credits left this week: 0 of 100"]);
    expect(allowanceParts({ weeklyCreditCap: 100 })).toEqual(["Firecrawl credits left this week: 100 of 100"]);
  });
});

describe("employerLine (22 Nov)", () => {
  it("never shows the board that posted a job as its employer", () => {
    expect(employerLine({ company: "eFinancialCareers", employerNamed: false })).toBe("Employer not named (via eFinancialCareers)");
  });
  it("shows an agency's repost under the employer it names, with the agency beside it", () => {
    expect(employerLine({ company: "Citi", postedBy: "eFinancialCareers", employerNamed: true })).toBe("Citi (via eFinancialCareers)");
    expect(employerLine({ company: "Citi" })).toBe("Citi");
  });
});
