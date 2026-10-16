import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** Gate 2 "Request changes": the note goes to the writer and the pack is drafted again. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/regenerate`);
}
