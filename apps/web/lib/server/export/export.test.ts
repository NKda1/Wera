import { inflateRawSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ApiError } from "../http";
import type { ApplicationWithJob, Cited, Contact, MasterCV, Pack } from "../../types";
import { exportDoc } from "./content";
import { renderExport } from "./index";

const fixture = <T>(name: string): T => JSON.parse(readFileSync(fileURLToPath(new URL(`../../../../../fixtures/demo/${name}`, import.meta.url)), "utf8")) as T;

const CV = fixture<MasterCV>("cv.json");
const CONTACT = fixture<Contact>("contact.json");

const BULLETS: Record<string, string> = {
  "exp-idsu-01": "Built a FastAPI API gateway that scans prompts for personal data (regex and Presidio) before they reach third-party LLMs.",
  "exp-idsu-03": "Wrote prompt templates and evaluation notes for the gateway's redaction rules with the security team.",
  "exp-fm-01": "Built and run a scouting platform for grassroots basketball clubs in Europe on Next.js, Supabase / Postgres and Stripe, with real users.",
  "exp-fm-03": "Added a LangGraph agent in Python that drafts weekly scouting summaries from coach notes for review.",
  "prj-oss-01": "Added structured-output retries with schema validation to a LangChain community model integration in Python.",
};

const cited = (id: string): Cited => ({ text: BULLETS[id], sourceIds: [id] });

const clean = { pass: true, violations: [], repaired: false };

const PACK: Pack = {
  cv: {
    jobId: "job-fernhill-mle",
    cvVersion: CV.version,
    summary: {
      text: "Computer Science graduate building production AI tooling: a FastAPI PII-detection gateway for LLM prompts, and a live TypeScript and Postgres platform with real users.",
      sourceIds: ["sum-01"],
    },
    sections: [
      { kind: "experience", items: [{ roleId: "exp-idsu", bullets: [cited("exp-idsu-01"), cited("exp-idsu-03")] }, { roleId: "exp-fm", bullets: [cited("exp-fm-01"), cited("exp-fm-03")] }] },
      { kind: "projects", items: [{ roleId: "prj-oss", bullets: [cited("prj-oss-01")] }] },
    ],
    skills: ["Python", "FastAPI", "LangGraph", "PostgreSQL", "LLM guardrails", "Presidio"],
    omittedBulletIds: ["exp-idsu-02", "exp-fm-02"],
    guard: clean,
  },
  letter: {
    jobId: "job-fernhill-mle",
    greeting: "Dear Hiring Team,",
    paragraphs: [
      { text: "I am applying for the Machine Learning Engineer role. At the IDSU I built a FastAPI gateway that scans prompts for personal data before they reach third-party models.", sourceIds: ["exp-idsu-01"] },
      { text: "Your post describes a team that ships guardrails as product, which is the work I have been doing on my own platform with real users.", sourceIds: ["exp-fm-01"] },
      { text: "I would welcome the chance to talk about the agent evaluation work you mention.", sourceIds: ["exp-fm-03"] },
    ],
    signoff: "Kind regards,\nJordan Ellery",
    companyFact: { quote: "ships guardrails as product" },
    wordCount: 268,
    guard: clean,
  },
  ats: { keywords: [{ term: "FastAPI", weight: "required", matched: true, inMasterCv: true }], coverage: 0.82, formatChecks: [], pages: 1, attempt: 1 },
  answers: [],
};

const APP: ApplicationWithJob = {
  id: "app-fernhill-mle-1",
  jobId: "job-fernhill-mle",
  status: "approved",
  createdAt: "2026-10-10T09:00:00.000Z",
  approvedAt: "2026-10-12T14:30:00.000Z",
  pack: PACK,
  job: {
    id: "job-fernhill-mle",
    title: "Machine Learning Engineer",
    company: "Fernhill Analytics",
    location: { raw: "London", city: "London" },
    remote: "hybrid",
    fitTotal: 8.4,
  },
};

const render = (kind: "cv" | "letter", format: "pdf" | "docx", app: ApplicationWithJob = APP) => renderExport(kind, format, app, CV, CONTACT, 1);

/** Reads one entry out of a zip (a .docx) without pulling in a dependency. */
function unzip(bytes: Uint8Array, entry: string): string {
  const buf = Buffer.from(bytes);
  const name = Buffer.from(entry, "utf8");
  for (let i = 0; i < buf.length - 4; i++) {
    if (buf.readUInt32LE(i) !== 0x04034b50) continue;
    const method = buf.readUInt16LE(i + 8);
    const compressed = buf.readUInt32LE(i + 18);
    const nameLen = buf.readUInt16LE(i + 26);
    const extraLen = buf.readUInt16LE(i + 28);
    if (nameLen !== name.length || !buf.subarray(i + 30, i + 30 + nameLen).equals(name)) continue;
    const start = i + 30 + nameLen + extraLen;
    const data = buf.subarray(start, start + compressed);
    return (method === 8 ? inflateRawSync(data) : data).toString("utf8");
  }
  throw new Error(`${entry} is not in the archive`);
}

const checks = (list: { id: string; pass: boolean; message: string }[]) => Object.fromEntries(list.map((c) => [c.id, c]));

describe("CV PDF", () => {
  it("passes the format lint (T-09: ATSC-05 and ATSC-06)", async () => {
    const out = await render("cv", "pdf");
    const failed = out.checks.filter((c) => !c.pass);
    expect(failed.map((c) => `${c.id}: ${c.message}`)).toEqual([]);
  });

  it("is one page and named First-Last-CV-Company.pdf (ATSC-05)", async () => {
    const out = await render("cv", "pdf");
    expect(out.fileName).toBe("Jordan-Ellery-CV-Fernhill-Analytics.pdf");
    expect(checks(out.checks).pages.message).toBe("1 page");
    expect(out.contentType).toBe("application/pdf");
  });

  it("inserts the real contact details at render time (EXP-02)", async () => {
    const doc = exportDoc("cv", APP, CV, CONTACT);
    expect(doc.header[0]).toContain(CONTACT.email);
    expect(doc.header[0]).toContain(CONTACT.phone!);
    // The review screen shows placeholders; the file must not.
    expect(doc.header.join(" ")).not.toContain("[email]");
  });

  it("takes role headers, dates and education from the master CV (TAIL-02)", () => {
    const rows = exportDoc("cv", APP, CV, CONTACT).blocks.filter((b) => b.t === "row");
    // 2026-02 → 2026-08 is one year, so the header carries the year once.
    expect(rows).toContainEqual({ t: "row", text: "AI Intern — International Digital Standards Union, Geneva", right: "2026" });
    expect(rows).toContainEqual({ t: "row", text: "Founder and Developer — Fieldmark, London", right: "2024 – present" });
    expect(rows).toContainEqual({ t: "row", text: "BSc Computer Science — University of Westmoor", right: "2026" });
  });

  it("reports a role the master CV no longer has instead of inventing a header (TAIL-02)", async () => {
    const stale: ApplicationWithJob = {
      ...APP,
      pack: { ...PACK, cv: { ...PACK.cv, sections: [{ kind: "experience", items: [{ roleId: "exp-gone", bullets: [cited("exp-idsu-01")] }] }] } },
    };
    const out = await render("cv", "pdf", stale);
    expect(checks(out.checks).roles_resolved).toMatchObject({ pass: false });
    expect(checks(out.checks).roles_resolved.message).toContain("exp-gone");
  });

  it("renders byte-identical files for the same pack (EXP-03)", async () => {
    const [a, b] = await Promise.all([render("cv", "pdf"), render("cv", "pdf")]);
    expect(Buffer.from(a.bytes).equals(Buffer.from(b.bytes))).toBe(true);
  });
});

describe("cover letter", () => {
  it("exports as its own file (EXP-01)", async () => {
    const out = await render("letter", "pdf");
    expect(out.fileName).toBe("Jordan-Ellery-Letter-Fernhill-Analytics.pdf");
    expect(out.checks.filter((c) => !c.pass).map((c) => c.id)).toEqual([]);
  });

  it("signs off with the name from the master CV", () => {
    const texts = exportDoc("letter", APP, CV, CONTACT).blocks.map((b) => b.text);
    expect(texts).toContain("Dear Hiring Team,");
    expect(texts.at(-1)).toBe(CV.person.name);
  });
});

describe("DOCX", () => {
  it("contains every bullet and heading (EXP-01)", async () => {
    const out = await render("cv", "docx");
    expect(out.fileName).toBe("Jordan-Ellery-CV-Fernhill-Analytics.docx");
    const xml = unzip(out.bytes, "word/document.xml");
    const text = xml
      .replace(/<[^>]+>/g, "")
      .replace(/&apos;/g, "'")
      .replace(/&quot;/g, '"')
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&");
    for (const id of ["exp-idsu-01", "exp-idsu-03", "exp-fm-01", "exp-fm-03", "prj-oss-01"]) {
      expect(text).toContain(BULLETS[id]);
    }
    for (const heading of ["SUMMARY", "EXPERIENCE", "PROJECTS", "EDUCATION", "SKILLS"]) expect(text).toContain(heading);
    expect(text).toContain(CONTACT.email);
    // ATSC-05: no tables.
    expect(xml).not.toContain("<w:tbl>");
  });

  it("leaves out the bullets the pack omitted", async () => {
    const xml = unzip((await render("cv", "docx")).bytes, "word/document.xml").replace(/<[^>]+>/g, "");
    expect(xml).not.toContain("Orchestrated agentic AI workflows");
  });

  it("renders identical content for the same pack (EXP-03)", async () => {
    const [a, b] = await Promise.all([render("cv", "docx"), render("cv", "docx")]);
    // The zip entries and docProps/core.xml carry the current time — the metadata
    // timestamps EXP-03 excludes. Everything that holds content must match exactly.
    for (const part of ["word/document.xml", "word/styles.xml", "word/numbering.xml"]) {
      expect(unzip(a.bytes, part)).toBe(unzip(b.bytes, part));
    }
  });
});

describe("the gate", () => {
  it("refuses a pack that hasn't been approved (G2-03)", async () => {
    const waiting: ApplicationWithJob = { ...APP, status: "ready_for_review", approvedAt: undefined };
    await expect(render("cv", "pdf", waiting)).rejects.toThrow(ApiError);
    await expect(render("cv", "pdf", waiting)).rejects.toThrow("Approve the pack to unlock downloads.");
  });

  it("still exports after the application moves on", async () => {
    for (const status of ["submitted", "interview", "offer"] as const) {
      const out = await render("cv", "docx", { ...APP, status });
      expect(out.bytes.byteLength).toBeGreaterThan(0);
    }
  });
});
