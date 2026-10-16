import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** Gate 2 approval. Only a person can move an application to approved (§7.2). */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/approve`);
}
