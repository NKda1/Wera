import { handle, readJson, type IdContext } from "@/lib/server/http";
import { DocumentPatchSchema, parse } from "@/lib/server/schemas";
import { editDocument } from "@/lib/server/store";

/** G2-02: in-place edits are saved and marked "edited by you"; the guard doesn't re-check them. */
export async function PATCH(req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(async () => editDocument(id, parse(DocumentPatchSchema, await readJson(req))));
}
