import { forward } from "@/lib/server/agent";

// CV-08 (18 Nov): re-score the board's jobs whose scores predate a CV change, offered once the
// review list has been re-scored. No fetching — the jobs are stored, their scores are just stale.
export async function POST(req: Request): Promise<Response> {
  return forward(req, "/intake/rescore");
}
