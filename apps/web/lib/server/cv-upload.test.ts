// CV-03: getting the words out of an uploaded file. What the words *mean* is the agent's job.

import { Document, Packer, Paragraph } from "docx";
import { describe, expect, it } from "vitest";
import { extractCvText, kindOf, MAX_BYTES } from "./cv-upload";
import { ApiError } from "./http";

const CV_LINES = [
  "Ada Whitfield",
  "Backend engineer working on payments infrastructure",
  "London, United Kingdom",
  "",
  "Experience",
  "Senior Backend Engineer, Quillpay — Mar 2023 – present",
  "- Rebuilt the settlement ledger, cutting reconciliation time from six hours to twelve minutes",
  "- Owned the Stripe webhook pipeline through two PCI audits",
  "",
  "Skills",
  "Python, PostgreSQL, FastAPI, Stripe, Docker",
];

function file(name: string, bytes: Uint8Array | string, size?: number): File {
  const f = new File([typeof bytes === "string" ? bytes : (bytes as unknown as BlobPart)], name);
  if (size !== undefined) Object.defineProperty(f, "size", { value: size });
  return f;
}

async function docx(lines: string[]): Promise<Uint8Array> {
  const doc = new Document({ sections: [{ children: lines.map((text) => new Paragraph(text)) }] });
  return new Uint8Array(await Packer.toBuffer(doc));
}

/** The error a caller actually sees, rather than letting the assertion swallow it. */
async function failure(fn: () => Promise<unknown>): Promise<ApiError> {
  try {
    await fn();
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error("expected an ApiError, but it succeeded");
}

describe("which files are accepted", () => {
  it.each([
    ["cv.pdf", "pdf"],
    ["My CV.PDF", "pdf"],
    ["cv.docx", "docx"],
    ["master-cv-v4.yaml", "yaml"],
    ["master-cv-v4.yml", "yaml"],
    ["cv.txt", "text"],
  ])("%s is read as %s", (name, kind) => {
    expect(kindOf(name)).toBe(kind);
  });

  it.each(["cv.doc", "cv.pages", "cv.png", "cv", "cv.pdf.exe"])("%s is refused", (name) => {
    expect(kindOf(name)).toBeNull();
  });

  it("names the formats it does take, rather than just saying no", async () => {
    const err = await failure(() => extractCvText(file("cv.pages", "x".repeat(500))));
    expect(err.code).toBe("BAD_FILE");
    expect(err.message).toContain("PDF, DOCX");
    expect(err.message).toContain("cv.pages");
  });

  it("says how big the file was and what the limit is", async () => {
    const err = await failure(() => extractCvText(file("cv.pdf", "x", MAX_BYTES + 1)));
    expect(err.status).toBe(413);
    expect(err.message).toContain("10 MB");
  });
});

describe("DOCX", () => {
  it("keeps the line structure the model reads headings and bullets off", async () => {
    const text = (await extractCvText(file("cv.docx", await docx(CV_LINES)))).text;
    const lines = text.split("\n").filter(Boolean);
    expect(lines[0]).toBe("Ada Whitfield");
    expect(lines).toContain("Experience");
    expect(lines).toContain("- Owned the Stripe webhook pipeline through two PCI audits");
  });

  it("reports the kind it read, so the route knows whether a model is needed", async () => {
    expect((await extractCvText(file("cv.docx", await docx(CV_LINES)))).kind).toBe("docx");
  });

  it("tells a .doc user what to do instead of failing silently", async () => {
    const err = await failure(() => extractCvText(file("cv.docx", "not a zip at all, just text".repeat(20))));
    expect(err.code).toBe("BAD_FILE");
    expect(err.message).toContain(".docx");
  });
});

describe("a scan with no text layer", () => {
  it("is named as a scan, because that is the actionable part", async () => {
    // A valid DOCX whose only content is too short to be a CV — the same shape a scanned
    // page produces, and the case where "nothing found" sends people hunting for a bug.
    const scanned = await docx(["Ada"]);
    const err = await failure(() => extractCvText(file("cv.docx", scanned)));
    expect(err.code).toBe("NO_TEXT");
    expect(err.message).toMatch(/scan/);
    expect(err.message).toMatch(/paste/i);
  });
});

describe("YAML and plain text", () => {
  it("passes YAML through untouched, so a WERA export round-trips without a model", async () => {
    const yaml = "version: 4\nperson:\n  name: Ada Whitfield\n";
    const out = await extractCvText(file("cv.yaml", yaml));
    expect(out.kind).toBe("yaml");
    expect(out.text).toContain("name: Ada Whitfield");
  });

  it("does not apply the scan check to YAML, which is legitimately short", async () => {
    await expect(extractCvText(file("cv.yml", "version: 1\n"))).resolves.toBeTruthy();
  });

  it("normalises the whitespace a PDF leaves behind without losing the lines", async () => {
    const messy = `Ada \u00a0 Whitfield\r\n London\r\n\n\n\nExperience   \r\n${"-  a bullet line that is long enough to clear the minimum ".repeat(3)}`;
    const { text } = await extractCvText(file("cv.txt", messy));
    expect(text).toContain("Ada Whitfield");
    expect(text).not.toContain("\r");
    expect(text).not.toContain("\u00a0");
    expect(text).not.toMatch(/\n{3,}/);
    expect(text.split("\n")).toContain("Experience");
  });
});
