import { forward } from "@/lib/server/agent";

/** CV-03: CV text pasted in rather than uploaded. Returns a proposal; nothing is saved. */
export async function POST(req: Request) {
  return forward(req, "/cv/parse");
}
