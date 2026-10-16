import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

export async function GET(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}`);
}

/** Tracker updates: status (only §7.2 transitions), notes and follow-up date. */
export async function PATCH(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}`);
}
