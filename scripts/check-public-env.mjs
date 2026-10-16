#!/usr/bin/env node
// SEC-03: no environment variable whose name contains KEY, SECRET, TOKEN or PASSWORD
// may be prefixed NEXT_PUBLIC_, because Next.js inlines those into the browser bundle.
// Checks every tracked or unignored file, and the current process environment.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";

const BAD = /\bNEXT_PUBLIC_[A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*/g;
const SELF = "scripts/check-public-env.mjs";

const files = execFileSync("git", ["ls-files", "--cached", "--others", "--exclude-standard", "-z"], { encoding: "utf8" })
  .split("\0")
  .filter((f) => f && f !== SELF && !/\.(png|jpe?g|gif|ico|woff2?|ttf|otf|pdf|zip|gz)$/i.test(f));

const hits = [];
for (const file of files) {
  let text;
  try {
    text = readFileSync(file, "utf8");
  } catch {
    continue; // deleted in the working tree
  }
  text.split("\n").forEach((line, i) => {
    for (const m of line.matchAll(BAD)) hits.push(`${file}:${i + 1}  ${m[0]}`);
  });
}
for (const name of Object.keys(process.env)) if (new RegExp(`^${BAD.source}$`).test(name)) hits.push(`environment  ${name}`);

if (hits.length) {
  console.error("check-public-env: secret-looking variables must never use the NEXT_PUBLIC_ prefix:");
  for (const h of hits) console.error(`  ${h}`);
  process.exit(1);
}
console.log(`check-public-env: ${files.length} files checked, no NEXT_PUBLIC_ secrets.`);
