import { handle, type IdContext } from "@/lib/server/http";
import { approvePack } from "@/lib/server/store";

/** Gate 2 approval. Only a person can move an application to approved (§7.2). */
export async function POST(_req: Request, { params }: IdContext) {
  const { id } = await params;
  return handle(() => approvePack(id));
}
