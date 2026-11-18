import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** The file as page images, for the editor's live preview. Saves nothing. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/cv-file/preview`);
}
