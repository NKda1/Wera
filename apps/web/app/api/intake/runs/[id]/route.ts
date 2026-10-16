import { handle, type IdContext } from "@/lib/server/http";
import { getRun } from "@/lib/server/store";

export async function GET(_req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(() => getRun(id));
}
