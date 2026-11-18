import { inflateRawSync } from "node:zlib";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { ApiError } from "../http";
import type { ApplicationWithJob, Contact, CvFile, MasterCV, Pack, TailoredCV } from "../../types";
import { exportDoc } from "./content";
import { type CvSource, lintExport, renderExport } from "./index";

const fixturePath = (name: string) => fileURLToPath(new URL(`../../../../../fixtures/demo/${name}`, import.meta.url));
const fixture = <T>(name: string): T => JSON.parse(readFileSync(fixturePath(name), "utf8")) as T;

const CV = fixture<MasterCV>("cv.json");
const CONTACT = fixture<Contact>("contact.json");
// The CV file and PDF the agent renders for this pack (`tests/test_cv_file.py` keeps them current):
// RenderCV runs in the agent, so these tests read its real output rather than a stand-in.
const FILE = fixture<CvFile>("rendercv/cv-file.json");
const CV_PDF = readFileSync(fixturePath("rendercv/cv.pdf"));

const BULLETS: Record<string, string> = {
  "exp-idsu-01": "Built a FastAPI API gateway that scans prompts for personal data (regex and Presidio) before they reach third-party LLMs.",
  "exp-idsu-03": "Wrote prompt templates and evaluation notes for the gateway's redaction rules with the security team.",
  "exp-fm-01": "Built and run a scouting platform for grassroots basketball clubs in Europe on Next.js, Supabase / Postgres and Stripe, with real users.",
  "exp-fm-03": "Added a LangGraph agent in Python that drafts weekly scouting summaries from coach notes for review.",
  "prj-oss-01": "Added structured-output retries with schema validation to a LangChain community model integration in Python.",
};

const clean = { pass: true, violations: [], repaired: false };

const PACK: Pack = {
  // Shared with the agent's test that renders the fixture above.
  cv: fixture<TailoredCV>("tailored-cv.json"),
  letter: {
    jobId: "job-fernhill-mle",
    greeting: "Dear Hiring Team,",
    paragraphs: [
      { text: "I am applying for the Machine Learning Engineer role. At the IDSU I built a FastAPI gateway that scans prompts for personal data before they reach third-party models.", sourceIds: ["exp-idsu-01"] },
      { text: "Your post describes a team that ships guardrails as product, which is the work I have been doing on my own platform with real users.", sourceIds: ["exp-fm-01"] },
      { text: "I would welcome the chance to talk about the agent evaluation work you mention.", sourceIds: ["exp-fm-03"] },
    ],
    signoff: "Kind regards,\nJordan Ellery",
    companyFact: { quote: "ships guardrails as product", policyQuote: "We are remote-first across the EU.", siteQuote: "" },
    wordCount: 268,
    guard: clean,
  },
  ats: { keywords: [{ term: "FastAPI", weight: "required", matched: true, inMasterCv: true }], coverage: 0.82, backedCoverage: 1, formatChecks: [], pages: 1, parts: [], suggestedSkills: [] },
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

const source = (file: CvFile = FILE, pdf: Uint8Array = CV_PDF): CvSource & { asked: number } => {
  const out = { file, asked: 0, pdf: async () => (out.asked++, new Uint8Array(pdf)) };
  return out;
};

const render = (kind: "cv" | "letter", format: "pdf" | "docx", app: ApplicationWithJob = APP, from: CvSource = source()) =>
  renderExport(kind, format, app, CV, CONTACT, 1, kind === "cv" ? from : undefined);

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
  it("passes the format lint on RenderCV's output (T-09: ATSC-05 and ATSC-06)", async () => {
    const out = await render("cv", "pdf");
    const failed = out.checks.filter((c) => !c.pass);
    expect(failed.map((c) => `${c.id}: ${c.message}`)).toEqual([]);
  });

  it("is the agent's file, one page, named First-Last-CV-Company.pdf (ATSC-05)", async () => {
    const out = await render("cv", "pdf");
    expect(Buffer.from(out.bytes).equals(CV_PDF)).toBe(true);
    expect(out.fileName).toBe("Jordan-Ellery-CV-Fernhill-Analytics.pdf");
    expect(checks(out.checks).pages.message).toBe("1 page");
    expect(out.contentType).toBe("application/pdf");
  });

  it("adds the real contact details at render time, and the file never holds them (EXP-02)", () => {
    const doc = exportDoc("cv", APP, CV, CONTACT, FILE);
    expect(doc.header[0]).toContain(CONTACT.email);
    expect(doc.header[0]).toContain(CONTACT.phone!);
    expect(doc.headerItems).toEqual([CONTACT.address, CONTACT.email, CONTACT.phone, ...FILE.links]);
    expect(FILE.yaml).not.toContain(CONTACT.email);
    expect(FILE.yaml).not.toContain("7700");
  });

  it("takes role headers, dates and education from the master CV, through the file (TAIL-02)", () => {
    const rows = exportDoc("cv", APP, CV, CONTACT, FILE).blocks.filter((b) => b.t === "row");
    // 2026-02 → 2026-08 is one year, so the header carries the year once.
    expect(rows).toContainEqual({ t: "row", text: "AI Intern — International Digital Standards Union, Geneva", right: "2026" });
    expect(rows).toContainEqual({ t: "row", text: "Founder and Developer — Fieldmark, London", right: "2024 – present" });
    expect(rows).toContainEqual({ t: "row", text: "BSc Computer Science — University of Westmoor", right: "2026" });
  });

  it("reports a role the master CV no longer has instead of inventing a header (TAIL-02)", async () => {
    const out = await render("cv", "pdf", APP, source({ ...FILE, unresolvedRoleIds: ["exp-gone"] }));
    expect(checks(out.checks).roles_resolved).toMatchObject({ pass: false });
    expect(checks(out.checks).roles_resolved.message).toContain("exp-gone");
  });

  it("notices a bullet the PDF lost", async () => {
    const blocks = [...FILE.blocks, { t: "bullet" as const, text: "A line that the rendered file does not contain at all, anywhere." }];
    const out = await render("cv", "pdf", APP, source({ ...FILE, blocks }));
    expect(checks(out.checks).text_extractable).toMatchObject({ pass: false });
  });

  it("lints a pack still under review, the same way the download does (ATSC-05/06)", async () => {
    const reviewing: ApplicationWithJob = { ...APP, status: "ready_for_review", approvedAt: undefined };
    await expect(render("cv", "pdf", reviewing)).rejects.toThrow(ApiError);
    const lint = await lintExport("cv", reviewing, CV, CONTACT, 1, source());
    expect(lint.map((c) => c.id)).toEqual((await render("cv", "pdf")).checks.map((c) => c.id));
    expect(lint.filter((c) => !c.pass).map((c) => `${c.id}: ${c.message}`)).toEqual([]);
  });
});

describe("cover letter", () => {
  it("exports as its own file (EXP-01)", async () => {
    const out = await render("letter", "pdf");
    expect(out.fileName).toBe("Jordan-Ellery-Letter-Fernhill-Analytics.pdf");
    expect(out.checks.filter((c) => !c.pass).map((c) => c.id)).toEqual([]);
  });

  it("renders byte-identical files for the same pack (EXP-03)", async () => {
    const [a, b] = await Promise.all([render("letter", "pdf"), render("letter", "pdf")]);
    expect(Buffer.from(a.bytes).equals(Buffer.from(b.bytes))).toBe(true);
  });

  it("signs off with the name from the master CV", () => {
    const texts = exportDoc("letter", APP, CV, CONTACT).blocks.map((b) => b.text);
    expect(texts).toContain("Dear Hiring Team,");
    expect(texts.at(-1)).toBe(CV.person.name);
  });

  it("is dated and addressed, above the greeting", () => {
    // The furniture a letter is supposed to carry. Without it the PDF opened on "Dear Hiring
    // Team," — an email pasted into a page. Both lines come from data WERA already holds, so
    // this is also the check that neither is invented: the date is the approval date (EXP-03)
    // and the company and role are the job's own.
    const texts = exportDoc("letter", APP, CV, CONTACT).blocks.map((b) => b.text);
    expect(texts.slice(0, 4)).toEqual(["12 October 2026", "Fernhill Analytics", "Re: Machine Learning Engineer", "Dear Hiring Team,"]);
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

  it("is the CV the person wrote, when they rewrote its file", async () => {
    const blocks = FILE.blocks.map((b) => (b.text === BULLETS["exp-fm-01"] ? { ...b, text: "My own line about Fieldmark." } : b));
    const xml = unzip((await render("cv", "docx", APP, source({ ...FILE, blocks, editedByUser: true }))).bytes, "word/document.xml");
    expect(xml).toContain("My own line about Fieldmark.");
    expect(xml).not.toContain("scouting platform");
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
  it("refuses a pack that hasn't been approved, before the agent renders anything (G2-03)", async () => {
    const waiting: ApplicationWithJob = { ...APP, status: "ready_for_review", approvedAt: undefined };
    const from = source();
    await expect(render("cv", "pdf", waiting, from)).rejects.toThrow(ApiError);
    await expect(render("cv", "pdf", waiting, from)).rejects.toThrow("Approve the pack to unlock downloads.");
    expect(from.asked).toBe(0);
  });

  it("still exports after the application moves on", async () => {
    for (const status of ["submitted", "interview", "offer"] as const) {
      const out = await render("cv", "docx", { ...APP, status });
      expect(out.bytes.byteLength).toBeGreaterThan(0);
    }
  });
});
