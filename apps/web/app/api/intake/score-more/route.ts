import { forward } from "@/lib/server/agent";

// MATCH-05: score the next batch from the pool the broad search already found. No channels and
// no fetching — the jobs are stored, they just have no score yet.
export async function POST(req: Request): Promise<Response> {
  return forward(req, "/intake/score-more");
}
