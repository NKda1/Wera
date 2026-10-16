import { handle, type IdContext } from "@/lib/server/http";
import { getJob } from "@/lib/server/store";

export async function GET(_req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(() => getJob(id));
}
