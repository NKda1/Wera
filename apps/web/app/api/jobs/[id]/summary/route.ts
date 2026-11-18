import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** The job's post in a minute's reading: written on click (one fast-tier call) and kept until the post changes. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/jobs/${seg(id)}/summary`);
}
