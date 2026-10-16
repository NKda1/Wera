import { handle, type IdContext } from "@/lib/server/http";
import { exportDocument } from "@/lib/server/store";

/** EXP-01/02: contact details are inserted here, on the server, at render time. */
export async function GET(req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(() => exportDocument(id, new URL(req.url).searchParams.get("format") ?? "pdf"));
}
