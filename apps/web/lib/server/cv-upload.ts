import "server-only";

// CV-03: an uploaded CV, turned into plain text for the agent to structure.
//
// This lives in the web app for the same reason PDF *export* does (ARCHITECTURE §4): the
// libraries are here. The agent never receives a file — it receives text — so a corrupt
// document fails in this process, and a 6 MB PDF never crosses the loopback boundary.
//
// Nothing here interprets the text. Which line is a job title and which is a date is the
// model's job (`cv.parse`), and the ids come from `domain/cv_build.py`. All this does is get
// the words out in the right order.

import mammoth from "mammoth";
import { extractText, getDocumentProxy } from "unpdf";
import { ApiError } from "./http";

/** A CV is one or two pages. Past this it's a portfolio or a thesis, and the agent truncates
 *  it anyway — refusing here means the person hears why instead of wondering. */
export const MAX_BYTES = 10 * 1024 * 1024;

/** Below this there's nothing a model could read, and a scan is the usual reason. */
const MIN_CHARS = 120;

export type CvFileKind = "pdf" | "docx" | "yaml" | "text";

const EXTENSIONS: Record<string, CvFileKind> = {
  pdf: "pdf",
  docx: "docx",
  yaml: "yaml",
  yml: "yaml",
  txt: "text",
  md: "text",
};

export const ACCEPT = ".pdf,.docx,.yaml,.yml,.txt,.md";

export function kindOf(filename: string): CvFileKind | null {
  const ext = filename.toLowerCase().split(".").pop() ?? "";
  return EXTENSIONS[ext] ?? null;
}

/** Collapses the runs of whitespace a PDF extractor leaves behind, without losing the line
 *  breaks — the model reads headings and bullets off those lines. */
function tidy(raw: string): string {
  return raw
    .replace(/\r\n?/g, "\n")
    .replace(/[\u00a0\u2007\u202f]/g, " ") // the non-breaking spaces PDF extractors emit
    .split("\n")
    .map((line) => line.replace(/[ \t]+/g, " ").trimEnd())
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

async function fromPdf(bytes: Uint8Array): Promise<string> {
  let pages: string[];
  try {
    const pdf = await getDocumentProxy(bytes);
    ({ text: pages } = await extractText(pdf, { mergePages: false }));
  } catch {
    throw new ApiError(422, "BAD_FILE", "That PDF couldn't be opened. If it's password-protected, remove the password and try again.");
  }
  // Page order is the reading order; a blank page between sections is not a paragraph break.
  return tidy(pages.filter((p) => p.trim()).join("\n\n"));
}

async function fromDocx(bytes: Uint8Array): Promise<string> {
  try {
    // `extractRawText` keeps paragraph breaks and drops styling, which is exactly the shape
    // the model wants: bullets stay on their own lines, fonts carry no meaning.
    const { value } = await mammoth.extractRawText({ buffer: Buffer.from(bytes) });
    return tidy(value);
  } catch {
    throw new ApiError(422, "BAD_FILE", "That DOCX couldn't be read. If it's an older .doc file, save it as .docx first.");
  }
}

export type Extracted = { kind: CvFileKind; text: string };

/** The uploaded file as text, or an `ApiError` saying what to do about it. */
export async function extractCvText(file: File): Promise<Extracted> {
  const kind = kindOf(file.name);
  if (!kind) {
    throw new ApiError(422, "BAD_FILE", `WERA can read PDF, DOCX and its own YAML. "${file.name}" isn't one of those.`);
  }
  if (file.size > MAX_BYTES) {
    throw new ApiError(
      413,
      "FILE_TOO_LARGE",
      `That file is ${(file.size / 1024 / 1024).toFixed(1)} MB and the limit is ${MAX_BYTES / 1024 / 1024} MB. A CV this large is usually a scan.`,
    );
  }

  const bytes = new Uint8Array(await file.arrayBuffer());
  const text = kind === "pdf" ? await fromPdf(bytes) : kind === "docx" ? await fromDocx(bytes) : tidy(new TextDecoder().decode(bytes));

  // A scanned CV is a real and common case, and "nothing was found" sends people looking for a
  // bug. Naming the cause is the difference between a dead end and a next step.
  if (kind !== "yaml" && text.length < MIN_CHARS) {
    throw new ApiError(
      422,
      "NO_TEXT",
      text.length === 0
        ? "That file has no text in it — it's an image or a scan. Export the CV from your word processor, or paste the text instead."
        : "That file has almost no text in it, so it's probably a scan. Export it from your word processor, or paste the text instead.",
    );
  }
  return { kind, text };
}
