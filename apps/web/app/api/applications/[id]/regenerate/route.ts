import { handle, readJson, type IdContext } from "@/lib/server/http";
import { regenerate } from "@/lib/server/store";

/** Gate 2 "Request changes": the note goes to the writer and the pack is drafted again. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(async () => {
    const body = await readJson<{ note?: string }>(req);
    return regenerate(id, typeof body.note === "string" ? body.note.slice(0, 1000) : "");
  });
}
