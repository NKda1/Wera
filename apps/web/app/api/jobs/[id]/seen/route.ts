import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** SRCH-25: the person opened this job's details, so it is no longer new. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/jobs/${seg(id)}/seen`);
}
