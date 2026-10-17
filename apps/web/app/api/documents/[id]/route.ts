import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** G2-02: in-place edits are saved as a new version and marked "edited by you"; the guard doesn't re-check them. */
export async function PATCH(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/documents/${seg(id)}`);
}
