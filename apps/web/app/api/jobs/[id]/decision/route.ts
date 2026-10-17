import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** Gate 1: approve, snooze (1, 3 or 7 days) or reject (G1-02). Every decision is logged (MATCH-06). */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/jobs/${seg(id)}/decision`);
}
