import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** Redraft the cover letter alone, with the company brief. The pack goes back to drafting until it's done. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/letter/rewrite`);
}
