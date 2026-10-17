import { forward } from "@/lib/server/agent";

/** Filters: status, sort, minScore, channel, location, remote (G1-04). */
export async function GET(req: Request) {
  return forward(req, "/jobs");
}
