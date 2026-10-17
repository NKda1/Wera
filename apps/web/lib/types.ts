// Data contracts (SPEC §6). The agent's Pydantic models are canonical: they export JSON
// Schema to packages/schemas/json, and contracts.gen.ts is generated from it (SCH-03).
// Change a contract in services/agent/wera_agent/schemas, then regenerate:
//   uv run --project services/agent wera schemas && pnpm --filter web gen:contracts

export type * from "./contracts.gen";
