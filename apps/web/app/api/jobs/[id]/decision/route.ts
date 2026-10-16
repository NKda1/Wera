import { handle, readJson, type IdContext } from "@/lib/server/http";
import { DecisionSchema, parse } from "@/lib/server/schemas";
import { decide } from "@/lib/server/store";

/** Gate 1: approve, snooze (1, 3 or 7 days) or reject (G1-02). Every decision is logged (MATCH-06). */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(async () => decide(id, parse(DecisionSchema, await readJson(req))));
}
