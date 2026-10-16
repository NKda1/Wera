#!/usr/bin/env node
// Generates lib/contracts.gen.ts from packages/schemas/json/responses.schema.json, which is
// exported from the agent's Pydantic models (SCH-03). Run after changing a Pydantic model:
//   uv run --project services/agent wera schemas && pnpm --filter web gen:contracts
// `--check` fails instead of writing when the file is out of date (used in CI).

import { readFileSync, writeFileSync } from "node:fs";
import { compile } from "json-schema-to-typescript";

const schemaPath = new URL("../../../packages/schemas/json/responses.schema.json", import.meta.url);
const outPath = new URL("../lib/contracts.gen.ts", import.meta.url);

const schema = JSON.parse(readFileSync(schemaPath, "utf8"));
const { title: _title, description: _description, ...defsOnly } = schema;

let ts = await compile({ ...defsOnly, title: "WeraContracts" }, "WeraContracts", {
  additionalProperties: false,
  unreachableDefinitions: true,
  declareExternallyReferenced: true,
  bannerComment: "// Generated from the agent's Pydantic models (packages/schemas/json). Do not edit.\n// Regenerate: uv run --project services/agent wera schemas && pnpm --filter web gen:contracts",
  style: { printWidth: 140, semi: true },
});
// The root type only exists to hold $defs, and the generator's per-type comments are noise.
ts = ts
  .replace(/export interface WeraContracts \{[^}]*\}\n?/, "")
  .replace(/\n \*\n \* This interface was referenced by `WeraContracts`'s JSON-Schema\n \* via the `definition` "\w+"\./g, "")
  .replace(/\/\*\*\n \* This interface was referenced by `WeraContracts`'s JSON-Schema\n \* via the `definition` "\w+".\n \*\/\n/g, "");

if (process.argv.includes("--check")) {
  let current = "";
  try {
    current = readFileSync(outPath, "utf8");
  } catch {}
  if (current !== ts) {
    console.error("gen-contracts: lib/contracts.gen.ts is out of date. Run `pnpm --filter web gen:contracts`.");
    process.exit(1);
  }
  console.log("gen-contracts: up to date.");
} else {
  writeFileSync(outPath, ts);
  console.log("gen-contracts: wrote lib/contracts.gen.ts");
}
