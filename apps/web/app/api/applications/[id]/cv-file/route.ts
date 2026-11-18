import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** EXP-01: the RenderCV file a pack's CV PDF is made from. No contact details travel with it. */
export async function GET(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/cv-file`);
}

/** Save the person's file, or reset it to WERA's. Saved as a new version and marked as theirs (G2-02). */
export async function PUT(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/cv-file`);
}
