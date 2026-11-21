import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** ATSC-09: how the CV reads — action verbs, numbers, summary length, skills count. Suggestions only. */
export async function GET(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/writing`);
}
