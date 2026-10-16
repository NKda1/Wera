#!/usr/bin/env node
// SEC-02 / T-02: fail if anything key-like is in the built browser bundle.
// Usage: node scripts/scan-bundle.mjs [dir]   (default apps/web/.next/static; run after `next build`)
// Matches are reported by file and rule only; the matched text is never printed.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const dir = process.argv[2] ?? "apps/web/.next/static";

const RULES = [
  { id: "openrouter-key", re: /sk-or-v1-[A-Za-z0-9]{16,}/ },
  { id: "firecrawl-key", re: /\bfc-[A-Fa-f0-9]{24,}/ },
  { id: "openai-style-key", re: /\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}/ },
];

// Real values from this machine's environment, when set, are searched for literally too.
const SECRET_ENV = ["LLM_API_KEY", "FIRECRAWL_API_KEY", "ALERTS_IMAP_APP_PASSWORD", "AGENT_TOKEN"];
const literals = SECRET_ENV.map((name) => ({ id: `value-of-${name}`, value: process.env[name]?.trim() })).filter((l) => l.value && l.value.length >= 8);

const TEXT = /\.(js|mjs|cjs|css|json|html|txt|map|svg)$/;

function* walk(path) {
  for (const entry of readdirSync(path)) {
    const full = join(path, entry);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (TEXT.test(entry)) yield full;
  }
}

let files;
try {
  files = [...walk(dir)];
} catch {
  console.error(`scan-bundle: ${dir} not found. Run the production build first.`);
  process.exit(2);
}

const hits = [];
for (const file of files) {
  const text = readFileSync(file, "utf8");
  for (const r of RULES) if (r.re.test(text)) hits.push({ file, rule: r.id });
  for (const l of literals) if (text.includes(l.value)) hits.push({ file, rule: l.id });
}

if (hits.length) {
  console.error(`scan-bundle: ${hits.length} key-like match(es) in the client bundle:`);
  for (const h of hits) console.error(`  ${relative(process.cwd(), h.file)}  [${h.rule}]`);
  process.exit(1);
}
console.log(`scan-bundle: ${files.length} files in ${dir}, no key-like strings.`);
