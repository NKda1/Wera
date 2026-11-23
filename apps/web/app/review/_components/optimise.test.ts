import { describe, expect, it } from "vitest";
import type { OptimiseChange, Pack } from "@/lib/types";
import { optimiseView, termSpans } from "./optimise";

const change = (over: Partial<OptimiseChange> = {}): OptimiseChange => ({
  location: "s0.i0.b1",
  roleId: "exp-1",
  before: "Built the gateway.",
  after: "Built the gateway on Azure.",
  keyword: "Azure",
  reason: "Adds Azure from the same role.",
  sourceIds: ["exp-1-01", "exp-1-02"],
  ...over,
});

const pack = (bullets: string[], changes: OptimiseChange[] | null): Pack =>
  ({
    cv: { sections: [{ kind: "experience", items: [{ roleId: "exp-1", bullets: bullets.map((text) => ({ text, sourceIds: ["exp-1-01"] })) }] }] },
    optimise: changes ? { scoreBefore: 61.6, scoreAfter: 71.2, rounds: [], changes, refused: [], calls: 1, stopped: "no_gain" } : undefined,
  }) as unknown as Pack;

describe("termSpans", () => {
  it("finds the keyword however it is cased, and never inside a longer word", () => {
    expect(termSpans("Azure and azure, not Azurest", "Azure")).toEqual([
      { start: 0, end: 5 },
      { start: 10, end: 15 },
    ]);
  });
});

describe("optimiseView", () => {
  it("is null when the optimiser changed nothing, or didn't run", () => {
    expect(optimiseView(pack(["Built the gateway."], null))).toBeNull();
    expect(optimiseView(pack(["Built the gateway."], []))).toBeNull();
  });

  it("gives the scores, and where the changed line is now so it can be undone", () => {
    const view = optimiseView(pack(["Led the team.", "Built the gateway on Azure."], [change()]))!;
    expect([view.from, view.to]).toEqual([62, 71]);
    expect(view.changes[0].state).toBe("applied");
    expect(view.changes[0].target).toBe("s0.i0.b1");
    expect(view.changes[0].after.filter((s) => s.mark).map((s) => s.text)).toEqual(["Azure"]);
  });

  it("finds the line by its text when lines have moved since", () => {
    const view = optimiseView(pack(["Built the gateway on Azure."], [change()]))!;
    expect(view.changes[0].target).toBe("s0.i0.b0");
  });

  it("knows a line that was undone, and one rewritten by hand since, which it won't undo", () => {
    expect(optimiseView(pack(["Built the gateway."], [change()]))!.changes[0]).toMatchObject({ state: "undone", target: undefined });
    expect(optimiseView(pack(["My own words."], [change()]))!.changes[0]).toMatchObject({ state: "edited", target: undefined });
  });
});
