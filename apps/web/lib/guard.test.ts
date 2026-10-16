import { describe, expect, it } from "vitest";
import { demoCv } from "./demo/cv";
import { checkCited } from "./guard";

const cite = (text: string, ids = ["exp-idsu-01"]) => [{ location: "b0", item: { text, sourceIds: ids } }];

describe("citation guard (§8.5)", () => {
  it("passes a line that stays within its source", () => {
    expect(checkCited(cite("Built a FastAPI gateway that scans prompts for personal data before they reach LLMs."), demoCv).pass).toBe(true);
  });

  it("fails a skill the cited line doesn't mention (T-05)", () => {
    const r = checkCited(cite("Built a FastAPI gateway in Docker that scans prompts for personal data."), demoCv);
    expect(r.pass).toBe(false);
    expect(r.violations[0].rule).toBe("terms");
  });

  it("fails a number that isn't in the source", () => {
    const r = checkCited(cite("Cut prompt leaks by 40% with a FastAPI gateway."), demoCv);
    expect(r.violations.some((v) => v.rule === "numbers")).toBe(true);
  });

  it("fails unknown and missing source ids", () => {
    expect(checkCited(cite("Did things.", ["exp-nope-01"]), demoCv).pass).toBe(false);
    expect(checkCited(cite("Did things.", []), demoCv).pass).toBe(false);
  });

  it("does not re-check lines the user edited (G2-02)", () => {
    const items = [{ location: "b0", item: { text: "Ran Docker at 40% scale.", sourceIds: ["exp-idsu-01"], editedByUser: true } }];
    expect(checkCited(items, demoCv).pass).toBe(true);
  });

  it("accepts a skill from the master skills list even if the line lacks it", () => {
    expect(checkCited(cite("Built a Python service with FastAPI."), demoCv).pass).toBe(true);
  });
});
