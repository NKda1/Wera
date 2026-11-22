import "server-only";

// One content model behind all four exports (EXP-01). The DOCX renderer and the letter's PDF walk
// these blocks, and lint.ts checks each rendered PDF against the same blocks (ATSC-06) — so a
// heading or bullet can never be in one file and missing from the other.
//
// The CV's blocks are its RenderCV file's (18 Nov), read out by the agent, which renders the CV
// PDF from that same file: whatever the person wrote there is in the Word file too. TAIL-02 holds
// in the file: role headers, dates and education come from the master CV, never from the pack.
// EXP-02: contact details are read here, at render time, and in the agent's `export/cv_pdf.py`.

import { displayCase } from "../../format";
import type { ApplicationWithJob, Contact, CvFile, MasterCV } from "../../types";

export type ExportKind = "cv" | "letter";

export type Block =
  | { t: "heading"; text: string }
  | { t: "row"; text: string; right: string } // a line with right-aligned dates
  | { t: "bullet"; text: string }
  | { t: "body"; text: string };

export type ExportDoc = {
  kind: ExportKind;
  /** Name and contact lines, printed above the rule. */
  name: string;
  header: string[];
  /** The same details one by one, for the lint: RenderCV sets them on one line with its own separators. */
  headerItems?: string[];
  blocks: Block[];
  /** `First-Last-CV-Company`, without the extension (ATSC-05). */
  fileBase: string;
  title: string;
  /** EXP-03: fixed, so the same pack always renders the same bytes. */
  date: Date;
  /** Roles the pack cites that the master CV no longer has; lint reports them. */
  unresolvedRoleIds: string[];
};

/** `Jordan Ellery` + `Fernhill Analytics` → `Jordan-Ellery-CV-Fernhill-Analytics` (ATSC-05). */
function fileBase(name: string, kind: ExportKind, company: string): string {
  const part = (s: string) =>
    s
      .normalize("NFKD")
      .replace(/[^\p{L}\p{N} ]+/gu, "")
      .trim()
      .split(/\s+/)
      .filter(Boolean)
      .join("-");
  return [part(name) || "CV", kind === "cv" ? "CV" : "Letter", part(company) || "Company"].join("-");
}

/** EXP-02. Where contact details enter a document in the web app. */
function contactLines(contact: Contact, place: string | undefined, links: string[]): string[] {
  const first = [contact.email, contact.phone, place].filter(Boolean).join(" · ");
  return links.length ? [first, links.join(" · ")] : [first];
}

const shortUrl = (url: string) => url.replace(/^https?:\/\//, "").replace(/\/$/, "");

/** The CV file's blocks as export blocks. A row always has a right-hand side, if only empty. */
function cvBlocks(file: CvFile): Block[] {
  return file.blocks.map((b) => (b.t === "row" ? { t: "row", text: b.text, right: b.right ?? "" } : { t: b.t, text: b.text }));
}

/** `2026-11-01` → `1 November 2026`. The date a letter is dated, not a timestamp. */
function letterDate(date: Date): string {
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

function letterBlocks(app: ApplicationWithJob, cv: MasterCV): Block[] {
  const letter = app.pack!.letter;
  // The signoff's second line is the sender's name, which comes from the master CV (TAIL-02).
  const signoff = letter.signoff.replace(/\n.*$/, `\n${displayCase(cv.person.name)}`);
  // The furniture a letter is supposed to carry and this one didn't: a date, and who it is
  // addressed to. Both come from data WERA already holds — the approval time (EXP-03) and the
  // job's own title and company — so nothing here is inferred, and a letter that opens straight
  // into "Dear Hiring Team," reads as an email someone pasted into a PDF.
  //
  // No street address: WERA has the employer's name, not their post room, and inventing one in
  // the place a reader expects a verifiable address is the wrong kind of tidy.
  const recipient = [
    { t: "body" as const, text: letterDate(new Date(app.approvedAt ?? app.createdAt)) },
    // 22 Nov: a board's or agency's post that names no employer is written to its hiring team,
    // as the letter is (`tailor.job_header`), never to the board as if it were hiring.
    { t: "body" as const, text: app.job.employerNamed === false ? `Hiring team, via ${app.job.company}` : app.job.company },
    { t: "body" as const, text: `Re: ${app.job.title}` },
  ];
  return [
    ...recipient,
    { t: "body", text: letter.greeting },
    ...letter.paragraphs.map((p): Block => ({ t: "body", text: p.text })),
    ...signoff.split("\n").map((line): Block => ({ t: "body", text: line })),
  ];
}

/**
 * Builds the content for one export. `app.pack` must be present and the pack approved —
 * the route checks that first. The CV needs its file (`agent.cvFile`); the letter doesn't.
 */
export function exportDoc(kind: ExportKind, app: ApplicationWithJob, cv: MasterCV, contact: Contact, file?: CvFile): ExportDoc {
  const common = {
    kind,
    fileBase: fileBase(cv.person.name, kind, app.job.company),
    title: `${cv.person.name} — ${kind === "cv" ? "CV" : "Cover letter"} — ${app.job.title}, ${app.job.company}`,
    // EXP-03: the approval time never changes, and a pack can't be edited once approved.
    date: new Date(app.approvedAt ?? app.createdAt),
  };
  if (kind === "letter") {
    const links = cv.person.links.map((l) => shortUrl(l.url));
    return { ...common, name: cv.person.name, header: contactLines(contact, contact.address ?? cv.person.location, links), blocks: letterBlocks(app, cv), unresolvedRoleIds: [] };
  }
  if (!file) throw new Error("The CV is exported from its RenderCV file: fetch it with agent.cvFile first.");
  // The agent adds the same details in the same order: place, email, phone, then the file's links.
  const place = contact.address ?? file.location;
  return {
    ...common,
    name: file.name,
    header: contactLines(contact, place, file.links),
    headerItems: [place, contact.email, contact.phone, ...file.links].filter((x): x is string => Boolean(x)),
    blocks: cvBlocks(file),
    unresolvedRoleIds: file.unresolvedRoleIds,
  };
}

/** What lint.ts expects to find in the rendered PDF (ATSC-06). */
export function expectedText(doc: ExportDoc): { headings: string[]; bodies: string[]; header: string[] } {
  return {
    headings: doc.blocks.filter((b) => b.t === "heading").map((b) => b.text),
    bodies: doc.blocks.filter((b) => b.t === "bullet" || b.t === "body").map((b) => b.text),
    // The name and contact line are what a parser reads first, so they are checked exactly.
    header: [doc.name, ...(doc.headerItems ?? doc.header)],
  };
}
