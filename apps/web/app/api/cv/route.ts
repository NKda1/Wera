import { forward } from "@/lib/server/agent";

/** The master CV without contact details (those are fetched separately; CV-06). */
export async function GET(req: Request) {
  return forward(req, "/cv");
}

export async function PUT(req: Request) {
  return forward(req, "/cv");
}
