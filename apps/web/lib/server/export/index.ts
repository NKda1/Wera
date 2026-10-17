import "server-only";

// EXP-01: the CV and the letter, as PDF and DOCX, from the same structured content.

import { renderToBuffer } from "@react-pdf/renderer";
import { ApiError } from "../http";
import type { ApplicationWithJob, Contact, MasterCV } from "../../types";
import { exportDoc, type ExportKind } from "./content";
import { renderDocx } from "./docx";
import { type FormatCheck, lintPdf } from "./lint";
import { paper } from "./paper";

export type Format = "pdf" | "docx";

export type Export = {
  bytes: Uint8Array;
  fileName: string;
  contentType: string;
  /** Present for PDFs; the lint reads the rendered file (ATSC-05, ATSC-06). */
  checks: FormatCheck[];
};

const TYPES: Record<Format, string> = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

/** G2-03: downloads unlock at Gate 2 and stay unlocked for the rest of the application's life. */
const DOWNLOADABLE = ["approved", "submitted", "interview", "offer"];

/** `app-fernhill-mle-1.cv` → the application id and which document. */
export function parseDocId(docId: string): { appId: string; kind: ExportKind } {
  const at = docId.lastIndexOf(".");
  const kind = at === -1 ? "" : docId.slice(at + 1);
  if (kind !== "cv" && kind !== "letter") {
    throw new ApiError(404, "NOT_FOUND", "Only the CV and the cover letter can be exported.");
  }
  return { appId: docId.slice(0, at), kind };
}

export function parseFormat(value: string | null): Format {
  if (value !== null && value !== "pdf" && value !== "docx") {
    throw new ApiError(422, "VALIDATION", "Format must be pdf or docx.");
  }
  return value ?? "pdf";
}

export async function renderExport(
  kind: ExportKind,
  format: Format,
  app: ApplicationWithJob,
  cv: MasterCV,
  contact: Contact,
  maxPages: number,
): Promise<Export> {
  if (!DOWNLOADABLE.includes(app.status) || !app.pack) {
    throw new ApiError(403, "PACK_NOT_APPROVED", "Approve the pack to unlock downloads.");
  }
  const doc = exportDoc(kind, app, cv, contact);
  const fileName = `${doc.fileBase}.${format}`;
  if (format === "docx") {
    return { bytes: await renderDocx(doc), fileName, contentType: TYPES.docx, checks: [] };
  }
  const bytes = new Uint8Array(await renderToBuffer(paper(doc)));
  return { bytes, fileName, contentType: TYPES.pdf, checks: await lintPdf(bytes, doc, fileName, maxPages) };
}
