import { describe, expect, it } from "vitest";
import { formatOrigin, formatPostedAge, isNew } from "./format";

describe("new jobs (SRCH-25)", () => {
  const foundBy = { runId: "run-7", channel: "board_search" as const, at: "2026-11-11T08:00:00Z" };

  it("is new until the person opens it", () => {
    expect(isNew({ foundBy })).toBe(true);
    expect(isNew({ foundBy, seenAt: "2026-11-11T09:00:00Z" })).toBe(false);
  });

  it("never calls a job from before tagging new", () => {
    expect(isNew({})).toBe(false);
  });

  it("names where it came from and when", () => {
    expect(formatOrigin({ foundBy })).toBe("Search 11 Nov");
    expect(formatOrigin({ foundBy: { ...foundBy, channel: "email_alert" } })).toBe("Email 11 Nov");
    expect(formatOrigin({ foundBy: { ...foundBy, channel: "watchlist" } })).toBe("Employer feed 11 Nov");
    expect(formatOrigin({})).toBe("");
  });
});

describe("posted age", () => {
  it("says when the source gave no date (SRCH-24)", () => {
    expect(formatPostedAge({ postedPrecision: "unknown" })).toBe("Date not given");
  });
});
