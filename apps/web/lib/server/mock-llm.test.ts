import { describe, expect, it } from "vitest";
import { demoCv } from "../demo/cv";
import { shortlistSeeds } from "../demo/jobs";
import type { Job } from "../types";
import { cvLineIndex, skillText } from "../vocab";
import { generatePack, tailoredText } from "./mock-llm";

const seed = shortlistSeeds[0]; // Fernhill AI, Greenhouse
const job = { id: seed.id, title: seed.title, company: seed.company, descriptionMd: seed.description } as Job;
const pack = generatePack({ job, evidence: seed.evidence, questions: seed.questions!, cv: demoCv, cvPages: 1 });
const lines = cvLineIndex(demoCv);

describe("mock writer", () => {
  it("cites a real master-CV line for every bullet and passes the guard (TAIL-03)", () => {
    const bullets = pack.cv.sections.flatMap((s) => s.items.flatMap((i) => i.bullets));
    expect(bullets.length).toBeGreaterThan(0);
    for (const b of bullets) {
      expect(b.sourceIds.length).toBeGreaterThan(0);
      expect(b.sourceIds.every((id) => lines.has(id))).toBe(true);
    }
    expect(pack.cv.guard.pass).toBe(true);
    expect(pack.letter.guard.pass).toBe(true);
  });

  it("only lists skills from the master CV (TAIL-04)", () => {
    const names = new Set(demoCv.skills.map((s) => s.name));
    expect(pack.cv.skills.every((s) => names.has(s))).toBe(true);
  });

  it("reports gaps the CV can't back as 'add only if true' and never invents them", () => {
    const gaps = pack.ats.keywords.filter((k) => !k.matched && !k.inMasterCv).map((k) => k.term);
    expect(gaps).toEqual(["Docker", "Model evaluation"]);
    expect(tailoredText(pack.cv)).not.toMatch(/docker/i);
    expect(Math.round(pack.ats.coverage * 100)).toBe(86);
  });

  it("quotes a company fact verbatim from the job (LTR-02)", () => {
    expect(pack.letter.companyFact.quote.length).toBeGreaterThan(20);
    expect(seed.description).toContain(pack.letter.companyFact.quote);
    expect(pack.letter.greeting).toBe("Dear Hiring Team,");
  });

  it("never drafts sensitive answers (ANS-02)", () => {
    const sensitive = pack.answers.filter((a) => a.sensitive);
    expect(sensitive.map((a) => a.question)).toEqual([
      "Are you legally authorised to work in the United Kingdom?",
      "Will you now or in the future require visa sponsorship?",
      "What are your salary expectations?",
    ]);
    expect(sensitive.every((a) => a.answer === null)).toBe(true);
  });

  it("records omitted lines so they can be added back (TAIL-06)", () => {
    const all = [...demoCv.experience, ...demoCv.projects].flatMap((r) => r.bullets.map((b) => b.id));
    const used = pack.cv.sections.flatMap((s) => s.items.flatMap((i) => i.bullets.flatMap((b) => b.sourceIds)));
    expect([...used, ...pack.cv.omittedBulletIds].sort()).toEqual(all.sort());
  });

  it("uses skill aliases when matching", () => {
    expect(skillText(demoCv.skills.find((s) => s.name === "PostgreSQL")!)).toContain("Postgres");
  });
});
