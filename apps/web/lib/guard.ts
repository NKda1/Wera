// Citation guard (SPEC §8.5): every model-written line must be traceable to the
// master-CV entries it cites. Runs before anything reaches the review screen.

import type { Cited, GuardResult, MasterCV } from "./types";
import { cvLineIndex, skillText, textHasTerm } from "./vocab";

/** Terms the guard watches for: master skills, aliases, job keywords and a common tech list. */
const COMMON_TECH = ["Docker", "Kubernetes", "AWS", "GCP", "Azure", "Rust", "Java", "React", "Django", "Kafka", "Terraform"];

const NUMBER = /\d+(?:[.,]\d+)?\s?%?/g;

export function checkCited(
  items: { location: string; item: Cited }[],
  cv: MasterCV,
  jobKeywords: string[] = [],
): GuardResult {
  const lines = cvLineIndex(cv);
  const masterSkills = cv.skills.map(skillText).join(" · ");
  const vocabulary = [...cv.skills.flatMap((s) => [s.name, ...(s.aliases ?? [])]), ...jobKeywords, ...COMMON_TECH];
  const violations: GuardResult["violations"] = [];

  for (const { location, item } of items) {
    if (item.editedByUser) continue; // G2-02: the user is the authority on their own edits
    if (item.sourceIds.length === 0) {
      violations.push({ location, rule: "sources", detail: "No master-CV source cited" });
      continue;
    }
    const missing = item.sourceIds.filter((id) => !lines.has(id));
    if (missing.length) {
      violations.push({ location, rule: "sources", detail: `Unknown source id: ${missing.join(", ")}` });
      continue;
    }
    const sourceText = item.sourceIds.map((id) => lines.get(id)!.text).join("\n");

    for (const n of item.text.match(NUMBER) ?? []) {
      if (!sourceText.includes(n.trim())) {
        violations.push({ location, rule: "numbers", detail: `"${n.trim()}" is not in the cited lines` });
      }
    }
    for (const term of vocabulary) {
      if (textHasTerm(item.text, term) && !textHasTerm(sourceText, term) && !textHasTerm(masterSkills, term)) {
        violations.push({ location, rule: "terms", detail: `"${term}" is not in the cited lines or your skills` });
      }
    }
  }
  return { pass: violations.length === 0, violations, repaired: false };
}
