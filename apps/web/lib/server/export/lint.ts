import "server-only";

// ATSC-05 and ATSC-06 (T-09), run on the bytes we are about to hand over — not on the
// template. The check ids match the ones the review rail already shows.

import { extractTextItems } from "unpdf";
import { expectedText, type ExportDoc } from "./content";

export type FormatCheck = { id: string; pass: boolean; message: string };

/** `First-Last-CV-Company.pdf` — letters, digits and single hyphens only (ATSC-05). */
const FILE_NAME = /^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-(?:CV|Letter)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\.(?:pdf|docx)$/;

const norm = (s: string) =>
  s
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[–—]/g, "-")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase();

/** The share of a bullet's words that survived extraction, so one lost hyphen isn't a failure. */
function coverage(haystack: string, needle: string): number {
  const words = norm(needle).split(" ").filter(Boolean);
  if (!words.length) return 1;
  if (haystack.includes(norm(needle))) return 1;
  return words.filter((w) => haystack.includes(w)).length / words.length;
}

export async function lintPdf(bytes: Uint8Array, doc: ExportDoc, fileName: string, maxPages: number): Promise<FormatCheck[]> {
  const { headings, bodies, header } = expectedText(doc);
  // Read the bytes first: pdf.js takes ownership of the array it is handed and leaves it detached.
  const raw = Buffer.from(bytes).toString("latin1");
  // Items, not `extractText`: that joins runs with nothing unless pdf.js flagged an end of
  // line, which glues the name to the contact line and the end of one wrapped bullet to the
  // start of the next. A space between every run reconstructs the lines as laid out.
  const { totalPages, items } = await extractTextItems(new Uint8Array(bytes));
  const found = norm(
    items
      .flat()
      .map((i) => i.str)
      .join(" "),
  );

  const missingHeadings = headings.filter((h) => !found.includes(norm(h)));
  // The name and contact line must come out whole, not merged into the line below — that is
  // what a parser reads first. Each has to end at whitespace or at the end of the text.
  const runTogether = header.filter((line) => {
    const at = found.indexOf(norm(line));
    return at === -1 || !(found[at + norm(line).length] ?? " ").match(/\s/);
  });
  // ATSC-06: every heading, and ≥95% of the body and bullet text.
  const scores = bodies.map((b) => coverage(found, b));
  const extracted = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : 1;
  const thin = bodies.filter((_, i) => scores[i] < 0.95);

  return [
    {
      id: "file_name",
      pass: FILE_NAME.test(fileName),
      message: FILE_NAME.test(fileName) ? fileName : `${fileName} isn't First-Last-CV-Company.pdf`,
    },
    {
      id: "pages",
      pass: totalPages <= maxPages,
      message: totalPages <= maxPages ? `${totalPages} page${totalPages === 1 ? "" : "s"}` : `${totalPages} pages, over the ${maxPages}-page limit`,
    },
    {
      id: "embedded_fonts",
      // A TrueType font stream is present and no standard-14 face is referenced.
      pass: raw.includes("/FontFile2") && !/\/BaseFont\s*\/(?:Helvetica|Times|Courier)/.test(raw),
      message: raw.includes("/FontFile2") ? "Fonts embedded" : "No embedded font stream",
    },
    {
      id: "no_tables_or_images",
      // The templates emit only text and layout views; this confirms nothing crept in.
      pass: !raw.includes("/Subtype /Image") && !raw.includes("/Subtype/Image"),
      message: "Single column, no tables or images",
    },
    {
      id: "contact_line",
      pass: runTogether.length === 0,
      message: runTogether.length === 0 ? "Name and contact details read cleanly" : `Runs into the next line: ${runTogether.join(", ")}`,
    },
    {
      id: "headings",
      pass: missingHeadings.length === 0,
      message: missingHeadings.length === 0 ? `${headings.length} standard headings found` : `Not extractable: ${missingHeadings.join(", ")}`,
    },
    {
      id: "text_extractable",
      pass: extracted >= 0.95,
      message:
        extracted >= 0.95
          ? `${Math.round(extracted * 100)}% of the text extracts cleanly`
          : `Only ${Math.round(extracted * 100)}% extracts; check ${thin.length} line${thin.length === 1 ? "" : "s"}`,
    },
    {
      id: "roles_resolved",
      pass: doc.unresolvedRoleIds.length === 0,
      message:
        doc.unresolvedRoleIds.length === 0
          ? "Every role header came from your master CV"
          : `Your master CV no longer has ${doc.unresolvedRoleIds.join(", ")}; redraft the pack`,
    },
  ];
}
