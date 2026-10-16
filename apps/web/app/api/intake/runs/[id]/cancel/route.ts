import { handle, type IdContext } from "@/lib/server/http";
import { cancelRun } from "@/lib/server/store";

export async function POST(_req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(() => cancelRun(id));
}
