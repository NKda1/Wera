import "server-only";

// Demo-mode stand-in for the writing tier (DEMO-01). It behaves like the contract in
// SPEC §9.3 without any network: it selects master-CV bullets for the job, keeps their
// text verbatim with real source ids, and computes the ATS report in code. The real
// model replaces `generatePack`; everything around it (guard, coverage, retries) stays.

import { checkCited } from "../guard";
import { atsCoverage, shouldRetryTailoring } from "../scoring";
import type {
  AnswerItem,
  AtsReport,
  Bullet,
  Cited,
  CoverLetter,
  FitEvidence,
  Job,
  MasterCV,
  Pack,
  Role,
  TailoredCV,
} from "../types";
import { masterCvText, skillText, textHasTerm } from "../vocab";

export interface PackInput {
  job: Job;
  evidence: FitEvidence;
  questions: string[];
  cv: MasterCV;
  cvPages: 1 | 2;
  note?: string;
}

const SENSITIVE =
  /(authori[sz]ed to work|right to work|visa|sponsor|salary|compensation|gender|ethnic|\brace\b|disabilit|veteran|criminal|convict|pronoun|sexual orientation|demographic|\bage\b)/i;

const lcFirst = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);
const noStop = (s: string) => s.replace(/\.\s*$/, "");
const words = (s: string) => s.split(/\s+/).filter(Boolean).length;

// ---------------------------------------------------------------------------
// Tailored CV

function selectBullets(
  input: PackInput,
  opts: { total: number; perRole: number; extraTerms: string[] },
): Map<string, Bullet[]> {
  const { evidence, cv } = input;
  const hints = noteHints(input.note, cv);
  const score = (b: Bullet) => {
    const text = `${b.text} ${b.tags.join(" ")}`;
    let n = 0;
    for (const k of evidence.keywords) if (textHasTerm(text, k.term)) n += k.weight === "required" ? 2 : 1;
    for (const t of [...hints, ...opts.extraTerms]) if (textHasTerm(text, t)) n += 4;
    if (evidence.cvBulletIds.includes(b.id)) n += 3;
    return n;
  };

  const candidates = [...cv.experience, ...cv.projects].flatMap((role) =>
    role.bullets.map((b, order) => ({ role, b, order, score: score(b) })),
  );
  candidates.sort((a, b) => b.score - a.score || a.order - b.order);

  const chosen = new Map<string, Bullet[]>();
  let count = 0;
  for (const c of candidates) {
    if (count >= opts.total || c.score <= 0) break;
    const list = chosen.get(c.role.id) ?? [];
    if (list.length >= opts.perRole) continue;
    list.push(c.b);
    chosen.set(c.role.id, list);
    count++;
  }
  // Keep master-CV order inside each role.
  for (const role of [...cv.experience, ...cv.projects]) {
    const list = chosen.get(role.id);
    if (list) list.sort((a, b) => role.bullets.indexOf(a) - role.bullets.indexOf(b));
  }
  return chosen;
}

function noteHints(note: string | undefined, cv: MasterCV): string[] {
  if (!note) return [];
  const terms = cv.skills.flatMap((s) => [s.name, ...(s.aliases ?? [])]);
  return terms.filter((t) => textHasTerm(note, t));
}

function tailorSkills(input: PackInput): string[] {
  const { cv, evidence } = input;
  const relevant = cv.skills.filter((s) => evidence.keywords.some((k) => textHasTerm(skillText(s), k.term)));
  const rest = cv.skills.filter((s) => !relevant.includes(s));
  return [...relevant, ...rest].slice(0, 10).map((s) => s.name);
}

function buildCv(input: PackInput, chosen: Map<string, Bullet[]>): TailoredCV {
  const { cv, job } = input;
  const cite = (b: Bullet): Cited => ({ text: b.text, sourceIds: [b.id] });
  const section = (kind: "experience" | "projects", roles: Role[]) => ({
    kind,
    items: roles.filter((r) => chosen.has(r.id)).map((r) => ({ roleId: r.id, bullets: chosen.get(r.id)!.map(cite) })),
  });
  const sections = [section("experience", cv.experience), section("projects", cv.projects)].filter((s) => s.items.length);
  const used = new Set([...chosen.values()].flat().map((b) => b.id));
  const omittedBulletIds = [...cv.experience, ...cv.projects].flatMap((r) => r.bullets.map((b) => b.id)).filter((id) => !used.has(id));
  const summary: Cited = cv.summary ? { text: cv.summary.text, sourceIds: [cv.summary.id] } : { text: "", sourceIds: [] };

  const tailored: TailoredCV = {
    jobId: job.id,
    cvVersion: cv.version,
    summary,
    sections,
    skills: tailorSkills(input),
    omittedBulletIds,
    guard: { pass: true, violations: [], repaired: false },
  };
  tailored.guard = guardCv(tailored, cv, input.evidence);
  return tailored;
}

export function guardCv(doc: TailoredCV, cv: MasterCV, evidence: FitEvidence) {
  const items = [
    ...(doc.summary.text ? [{ location: "summary", item: doc.summary }] : []),
    ...doc.sections.flatMap((s, si) =>
      s.items.flatMap((it, ii) => it.bullets.map((b, bi) => ({ location: `s${si}.i${ii}.b${bi}`, item: b }))),
    ),
  ];
  return checkCited(items, cv, evidence.keywords.map((k) => k.term));
}

// ---------------------------------------------------------------------------
// ATS report (§8.3, ATSC-05)

export function tailoredText(doc: TailoredCV): string {
  return [doc.summary.text, ...doc.sections.flatMap((s) => s.items.flatMap((i) => i.bullets.map((b) => b.text))), doc.skills.join(" · ")].join("\n");
}

export function estimatePages(doc: TailoredCV, cv: MasterCV): number {
  const lines = (chars: number, perLine: number) => Math.max(1, Math.ceil(chars / perLine));
  let total = 4; // name, contact line, rule, spacing
  total += lines(doc.summary.text.length, 95) + 0.5;
  for (const s of doc.sections) {
    total += 1.6; // heading
    for (const item of s.items) {
      total += 1.4; // role header
      for (const b of item.bullets) total += lines(b.text.length, 80) + 0.25;
    }
  }
  total += 1.6 + cv.education.length * 1.4; // education
  total += 1.6 + lines(doc.skills.join(" · ").length, 95); // skills
  return Math.round((total / 46) * 10) / 10;
}

export function buildAts(doc: TailoredCV, input: Pick<PackInput, "cv" | "evidence" | "cvPages" | "job">, attempt: number): AtsReport {
  const text = tailoredText(doc);
  const master = masterCvText(input.cv);
  const keywords = input.evidence.keywords.map((k) => ({
    term: k.term,
    weight: k.weight,
    matched: textHasTerm(text, k.term),
    inMasterCv: textHasTerm(master, k.term),
  }));
  const pages = estimatePages(doc, input.cv);
  const fileName = `${input.cv.person.name.replace(/\s+/g, "-")}-CV-${input.job.company.replace(/\W+/g, "-")}.pdf`;
  return {
    keywords,
    coverage: atsCoverage(keywords),
    pages,
    attempt,
    formatChecks: [
      { id: "single_column", pass: true, message: "Single column, standard headings" },
      { id: "extractable", pass: true, message: "Text-extractable PDF and DOCX" },
      { id: "no_tables", pass: true, message: "No tables, images or headers" },
      {
        id: "pages",
        pass: pages <= input.cvPages,
        message: pages <= input.cvPages ? `Fits on ${input.cvPages} page${input.cvPages > 1 ? "s" : ""}` : `Runs to ${pages} pages — trim to ${input.cvPages}`,
      },
      { id: "file_name", pass: true, message: `File name ${fileName}` },
    ],
  };
}

// ---------------------------------------------------------------------------
// Cover letter (LTR-01…05)

export function companyFact(description: string | null, company: string): string {
  if (!description) return "";
  const prose = description
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#") && !l.startsWith("-"));
  const line = prose.find((l) => l.includes(company)) ?? prose[0] ?? "";
  const sentence = line.match(/^.*?[.!?](?=\s|$)/)?.[0] ?? line;
  return sentence;
}

function namedContact(description: string | null): string | null {
  const m = description?.match(/hiring manager(?: is|:)\s+([A-Z][a-z]+ [A-Z][a-z]+)/i);
  return m ? m[1] : null;
}

function buildLetter(input: PackInput, doc: TailoredCV): CoverLetter {
  const { cv, job, evidence } = input;
  const roleOf = (id: string) => [...cv.experience, ...cv.projects].find((r) => r.id === id)!;
  const fact = companyFact(job.descriptionMd, job.company);
  const paragraphs: Cited[] = [];

  const intro = [`I'm applying for the ${job.title} role at ${job.company}.`];
  if (fact) intro.push(`Your post describes the company in one line: “${fact}”`);
  if (cv.summary) intro.push(`I'm a ${noStop(cv.summary.text)}, and that is the kind of work I want to keep doing.`);
  paragraphs.push({ text: intro.join(" "), sourceIds: cv.summary ? [cv.summary.id] : [] });

  const items = doc.sections.flatMap((s) => s.items);
  items.slice(0, 2).forEach((item, idx) => {
    const role = roleOf(item.roleId);
    const [first, second] = item.bullets;
    const parts = [`${idx === 0 ? "As" : "Separately, as"} ${role.title} at ${role.org}, I ${lcFirst(noStop(first.text))}.`];
    if (second) parts.push(`I also ${lcFirst(noStop(second.text))}.`);
    if (idx === 0 && evidence.jobQuote && job.descriptionMd?.includes(evidence.jobQuote)) {
      parts.push(`That is close to what you describe as “${evidence.jobQuote}”.`);
    }
    paragraphs.push({ text: parts.join(" "), sourceIds: item.bullets.slice(0, 2).flatMap((b) => b.sourceIds) });
  });

  const closing = `I'd welcome the chance to talk about how this experience could help ${job.company}, and to hear more about the team. Thank you for considering my application.`;
  const extra = items[2]?.bullets[0];
  if (extra) {
    const role = roleOf(items[2].roleId);
    paragraphs.push({ text: `Outside that, as ${role.title} for ${role.org}, I ${lcFirst(noStop(extra.text))}. ${closing}`, sourceIds: extra.sourceIds });
  } else {
    paragraphs.push({ text: closing, sourceIds: cv.summary ? [cv.summary.id] : [] });
  }

  const contact = namedContact(job.descriptionMd);
  const letter: CoverLetter = {
    jobId: job.id,
    greeting: contact ? `Dear ${contact},` : "Dear Hiring Team,",
    paragraphs,
    signoff: `Kind regards,\n${cv.person.name}`,
    companyFact: { quote: fact },
    wordCount: paragraphs.reduce((n, p) => n + words(p.text), 0),
    guard: { pass: true, violations: [], repaired: false },
  };
  letter.guard = guardLetter(letter, cv, evidence, job);
  return letter;
}

/** Claims about the user are checked; quotes and names from the job post are not (LTR-03). */
export function guardLetter(letter: CoverLetter, cv: MasterCV, evidence: FitEvidence, job: Pick<Job, "title" | "company">) {
  const strip = (text: string) =>
    [letter.companyFact.quote, evidence.jobQuote, job.title, job.company].filter(Boolean).reduce((t, q) => t.split(q).join(""), text);
  return checkCited(
    letter.paragraphs.map((p, i) => ({ location: `p${i}`, item: { ...p, text: strip(p.text) } })),
    cv,
    evidence.keywords.map((k) => k.term),
  );
}

// ---------------------------------------------------------------------------
// Answer pack (ANS-01…04)

function buildAnswers(input: PackInput, doc: TailoredCV): AnswerItem[] {
  const { cv, job, questions } = input;
  const lines = new Map([...cv.experience, ...cv.projects].flatMap((r) => r.bullets.map((b) => [b.id, { b, r }] as const)));
  return questions.map((question, i) => {
    const id = `q${i + 1}`;
    if (SENSITIVE.test(question)) return { id, question, answer: null, sourceIds: [], sensitive: true };
    if (/github|portfolio|website/i.test(question)) {
      const links = cv.person.links.map((l) => `${l.label}: ${l.url}`).join("\n");
      return { id, question, answer: links || null, sourceIds: [], sensitive: false };
    }
    if (/^why\b/i.test(question) && cv.summary) {
      const fact = companyFact(job.descriptionMd, job.company);
      const answer = `${fact ? `${fact} ` : ""}I'm a ${noStop(cv.summary.text)}. The ${job.title} role is the closest match I've seen to the work I've already done and want to keep doing.`;
      return { id, question, answer, sourceIds: [cv.summary.id], sensitive: false };
    }
    if (/tell us about|describe|example|a time you/i.test(question)) {
      const first = doc.sections[0]?.items[0]?.bullets[0];
      const hit = first && lines.get(first.sourceIds[0]);
      if (!hit) return { id, question, answer: null, sourceIds: [], sensitive: false };
      const answer = `As ${hit.r.title} at ${hit.r.org}, I ${lcFirst(noStop(hit.b.text))}.`;
      return { id, question, answer, sourceIds: [hit.b.id], sensitive: false };
    }
    return { id, question, answer: null, sourceIds: [], sensitive: false };
  });
}

// ---------------------------------------------------------------------------

export function generatePack(input: PackInput): Pack {
  const short = /\b(short|shorter|trim|tighter|one page|1 page)\b/i.test(input.note ?? "");
  let total = input.cvPages === 1 ? 5 : 9;
  const perRole = input.cvPages === 1 || short ? 2 : 3;
  if (short) total -= 1;

  // ATSC-03: re-tailor with missing-but-backed keywords as hints, max 2 retries.
  let attempt = 1;
  let extraTerms: string[] = [];
  let doc = buildCv(input, selectBullets(input, { total, perRole, extraTerms }));
  let ats = buildAts(doc, input, attempt);
  while (shouldRetryTailoring(ats) && attempt < 3) {
    attempt++;
    extraTerms = ats.keywords.filter((k) => !k.matched && k.inMasterCv).map((k) => k.term);
    total += 1;
    doc = buildCv(input, selectBullets(input, { total, perRole: perRole + 1, extraTerms }));
    ats = buildAts(doc, input, attempt);
  }

  return { cv: doc, letter: buildLetter(input, doc), ats, answers: buildAnswers(input, doc) };
}

/** Recompute the ATS report after the user edits a document (format and coverage only). */
export function refreshAts(pack: Pack, input: Pick<PackInput, "cv" | "evidence" | "cvPages" | "job">): AtsReport {
  return buildAts(pack.cv, input, pack.ats.attempt);
}
