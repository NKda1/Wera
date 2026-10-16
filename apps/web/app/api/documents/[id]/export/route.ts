import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** EXP-01/02: contact details are inserted on the server, at render time. */
export async function GET(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/documents/${seg(id)}/export`);
}
