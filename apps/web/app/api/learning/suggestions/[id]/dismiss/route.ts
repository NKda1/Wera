import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** MATCH-13: a learned suggestion the person doesn't want, not offered again. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/learning/suggestions/${seg(id)}/dismiss`);
}
