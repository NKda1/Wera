import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/intake/runs/${seg(id)}/cancel`);
}
