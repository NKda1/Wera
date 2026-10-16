import { handle, readJson, type IdContext } from "@/lib/server/http";
import { ApplicationPatchSchema, parse } from "@/lib/server/schemas";
import { getApplication, patchApplication } from "@/lib/server/store";

export async function GET(_req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(() => getApplication(id));
}

/** Tracker updates: status (only §7.2 transitions), notes and follow-up date. */
export async function PATCH(req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(async () => patchApplication(id, parse(ApplicationPatchSchema, await readJson(req))));
}
