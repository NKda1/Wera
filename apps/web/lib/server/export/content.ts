import "server-only";

// One content model behind all four exports (EXP-01). The PDF and DOCX renderers walk these
// blocks, and lint.ts checks the rendered PDF against the same blocks (ATSC-06) — so a heading
// or bullet can never be in one file and missing from the other.
//
// TAIL-02: role headers, dates and education come from the master CV, never from the pack.
// EXP-02: contact details are read here, at render time, and nowhere else in the app.

import { roleDates, year } from "../../format";
import type { ApplicationWithJob, Contact, MasterCV, Role } from "../../types";

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
  blocks: Block[];
  /** `First-Last-CV-Company`, without the extension (ATSC-05). */
  fileBase: string;
  title: string;
  /** EXP-03: fixed, so the same pack always renders the same bytes. */
  date: Date;
  /** Roles the pack cites that the master CV no longer has; lint reports them. */
  unresolvedRoleIds: string[];
};

const SECTION_HEADINGS: Record<"experience" | "projects", string> = { experience: "Experience", projects: "Projects" };

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

/** EXP-02. The one place contact details enter a document. */
function contactLines(cv: MasterCV, contact: Contact): string[] {
  const place = contact.address ?? cv.person.location;
  const first = [contact.email, contact.phone, place].filter(Boolean).join(" · ");
  const links = cv.person.links.map((l) => l.url.replace(/^https?:\/\//, "").replace(/\/$/, ""));
  return links.length ? [first, links.join(" · ")] : [first];
}

function cvBlocks(app: ApplicationWithJob, cv: MasterCV): { blocks: Block[]; unresolvedRoleIds: string[] } {
  const doc = app.pack!.cv;
  const roles = new Map<string, Role>([...cv.experience, ...cv.projects].map((r) => [r.id, r]));
  const blocks: Block[] = [];
  const unresolvedRoleIds: string[] = [];

  if (doc.summary.text.trim()) {
    blocks.push({ t: "heading", text: "Summary" }, { t: "body", text: doc.summary.text });
  }

  for (const section of doc.sections) {
    if (!section.items.length) continue;
    blocks.push({ t: "heading", text: SECTION_HEADINGS[section.kind] });
    for (const item of section.items) {
      const role = roles.get(item.roleId);
      if (!role) unresolvedRoleIds.push(item.roleId);
      blocks.push({
        t: "row",
        text: role ? `${role.title} — ${role.org}${role.location ? `, ${role.location}` : ""}` : item.roleId,
        right: role ? roleDates(role) : "",
      });
      for (const bullet of item.bullets) blocks.push({ t: "bullet", text: bullet.text });
    }
  }

  if (cv.education.length) {
    blocks.push({ t: "heading", text: "Education" });
    for (const ed of cv.education) {
      blocks.push({ t: "row", text: `${ed.degree} — ${ed.institution}`, right: year(ed.end) });
    }
  }

  if (doc.skills.length) {
    blocks.push({ t: "heading", text: "Skills" }, { t: "body", text: doc.skills.join(" · ") });
  }

  return { blocks, unresolvedRoleIds };
}

function letterBlocks(app: ApplicationWithJob, cv: MasterCV): Block[] {
  const letter = app.pack!.letter;
  // The signoff's second line is the sender's name, which comes from the master CV (TAIL-02).
  const signoff = letter.signoff.replace(/\n.*$/, `\n${cv.person.name}`);
  return [
    { t: "body", text: letter.greeting },
    ...letter.paragraphs.map((p): Block => ({ t: "body", text: p.text })),
    ...signoff.split("\n").map((line): Block => ({ t: "body", text: line })),
  ];
}

/**
 * Builds the content for one export. `app.pack` must be present and the pack approved —
 * the route checks that first.
 */
export function exportDoc(kind: ExportKind, app: ApplicationWithJob, cv: MasterCV, contact: Contact): ExportDoc {
  const { blocks, unresolvedRoleIds } = kind === "cv" ? cvBlocks(app, cv) : { blocks: letterBlocks(app, cv), unresolvedRoleIds: [] };
  return {
    kind,
    name: cv.person.name,
    header: contactLines(cv, contact),
    blocks,
    fileBase: fileBase(cv.person.name, kind, app.job.company),
    title: `${cv.person.name} — ${kind === "cv" ? "CV" : "Cover letter"} — ${app.job.title}, ${app.job.company}`,
    // EXP-03: the approval time never changes, and a pack can't be edited once approved.
    date: new Date(app.approvedAt ?? app.createdAt),
    unresolvedRoleIds,
  };
}

/** What lint.ts expects to find in the rendered PDF (ATSC-06). */
export function expectedText(doc: ExportDoc): { headings: string[]; bodies: string[]; header: string[] } {
  return {
    headings: doc.blocks.filter((b) => b.t === "heading").map((b) => b.text),
    bodies: doc.blocks.filter((b) => b.t === "bullet" || b.t === "body").map((b) => b.text),
    // The name and contact line are what a parser reads first, so they are checked exactly.
    header: [doc.name, ...doc.header],
  };
}
