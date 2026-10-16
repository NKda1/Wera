import { forward } from "@/lib/server/agent";

/** Returns a proposed CV; nothing is saved until PUT /api/cv (CV-03). */
export async function POST(req: Request) {
  return forward(req, "/cv/import");
}
