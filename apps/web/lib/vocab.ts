// Keyword normalisation (ATSC-01): a synonym map so "Postgres" counts as PostgreSQL.

import type { MasterCV } from "./types";

const SYNONYMS: Record<string, string[]> = {
  postgresql: ["postgresql", "postgres"],
  llms: ["llm", "llms", "large language model", "large language models"],
  agents: ["agent", "agents", "agentic"],
  pii: ["pii", "personal data"],
  "rest apis": ["rest api", "rest apis", "restful"],
  guardrails: ["guardrail", "guardrails"],
  prompting: ["prompting", "prompt engineering", "prompt templates"],
  "next.js": ["next.js", "nextjs"],
  "node.js": ["node.js", "nodejs", "node api"],
  "c# / .net": ["c#", ".net"],
  "model evaluation": ["model evaluation", "llm evaluation", "evals"],
  docker: ["docker"],
  rag: ["rag", "retrieval-augmented"],
  "ci/cd": ["ci/cd", "continuous integration"],
  go: ["golang"],
};

/** Short words that are also plain English only count when written as the proper noun. */
const CASE_SENSITIVE: Record<string, RegExp> = { go: /(^|[^A-Za-z0-9])Go($|[^A-Za-z0-9])/ };

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

export function aliasesFor(term: string): string[] {
  const key = term.toLowerCase().trim();
  return SYNONYMS[key] ?? [key];
}

/** Whole-word, case-insensitive match of a term or any of its synonyms. */
export function textHasTerm(text: string, term: string): boolean {
  if (CASE_SENSITIVE[term.toLowerCase()]?.test(text)) return true;
  const haystack = text.toLowerCase();
  return aliasesFor(term).some((alias) =>
    new RegExp(`(^|[^a-z0-9])${escape(alias)}($|[^a-z0-9])`).test(haystack),
  );
}

export function skillText(skill: MasterCV["skills"][number]): string {
  return [skill.name, ...(skill.aliases ?? [])].join(" · ");
}

/** Everything the master CV says, for "is this keyword backed by the CV?" checks. */
export function masterCvText(cv: MasterCV): string {
  const bullets = [...cv.experience, ...cv.projects].flatMap((r) => r.bullets.map((b) => `${b.text} ${b.tags.join(" ")}`));
  return [cv.person.headline ?? "", cv.summary?.text ?? "", ...bullets, ...cv.skills.map(skillText)].join("\n");
}

/** Every bullet in the CV, keyed by id, plus the summary. */
export function cvLineIndex(cv: MasterCV): Map<string, { text: string; where: string }> {
  const map = new Map<string, { text: string; where: string }>();
  if (cv.summary) map.set(cv.summary.id, { text: cv.summary.text, where: "Summary" });
  for (const role of [...cv.experience, ...cv.projects]) {
    for (const b of role.bullets) map.set(b.id, { text: b.text, where: `${role.title} — ${role.org}` });
  }
  for (const ed of cv.education) {
    for (const b of ed.notes ?? []) map.set(b.id, { text: b.text, where: ed.degree });
  }
  return map;
}
