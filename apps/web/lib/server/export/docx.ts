import "server-only";

// The DOCX twin of paper.tsx: the same Block list, the same single column, the same standard
// headings, and no tables or text boxes (ATSC-05). Dates sit on a right tab stop rather than
// in a table, so a parser reads each line left to right.

import { Document, Packer, Paragraph, Tab, TabStopPosition, TabStopType, TextRun, convertInchesToTwip } from "docx";
import type { ExportDoc } from "./content";

const FONT = "Source Sans 3";
const SIZE = 21; // half-points: 10.5pt

function paragraphs(doc: ExportDoc): Paragraph[] {
  const out: Paragraph[] = [
    new Paragraph({ children: [new TextRun({ text: doc.name, bold: true, size: 40 })] }),
    ...doc.header.map((line) => new Paragraph({ children: [new TextRun({ text: line, size: 19, color: "444444" })] })),
    new Paragraph({ text: "", border: { bottom: { style: "single", size: 8, color: "1A1A1A", space: 1 } }, spacing: { before: 120 } }),
  ];

  for (const block of doc.blocks) {
    switch (block.t) {
      case "heading":
        out.push(
          new Paragraph({
            children: [new TextRun({ text: block.text.toUpperCase(), bold: true, size: 20 })],
            spacing: { before: 240, after: 80 },
          }),
        );
        break;
      case "row":
        out.push(
          new Paragraph({
            tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
            children: [
              new TextRun({ text: block.text, bold: true }),
              ...(block.right ? [new TextRun({ children: [new Tab(), block.right], color: "444444" })] : []),
            ],
            spacing: { before: 120 },
          }),
        );
        break;
      case "bullet":
        out.push(new Paragraph({ text: block.text, bullet: { level: 0 }, spacing: { before: 40 } }));
        break;
      case "body":
        out.push(new Paragraph({ text: block.text, spacing: { before: 120 } }));
        break;
    }
  }
  return out;
}

export async function renderDocx(doc: ExportDoc): Promise<Uint8Array> {
  const file = new Document({
    creator: "WERA",
    title: doc.title,
    description: doc.kind === "cv" ? "Curriculum vitae" : "Cover letter",
    // EXP-03 holds for the document parts. `docx` stamps docProps/core.xml and the zip
    // entries with the current time and offers no option to fix them — those are the
    // metadata timestamps EXP-03 excludes.
    styles: { default: { document: { run: { font: FONT, size: SIZE, color: "1A1A1A" } } } },
    sections: [
      {
        properties: { page: { margin: { top: convertInchesToTwip(0.64), bottom: convertInchesToTwip(0.64), left: convertInchesToTwip(0.7), right: convertInchesToTwip(0.7) } } },
        children: paragraphs(doc),
      },
    ],
  });
  return new Uint8Array(await Packer.toBuffer(file));
}
