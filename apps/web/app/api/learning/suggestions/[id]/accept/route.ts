import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** MATCH-13: the person accepts a learned suggestion; it goes into their profile (PRF-04). */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/learning/suggestions/${seg(id)}/accept`);
}
