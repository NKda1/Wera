import { forward } from "@/lib/server/agent";

// SRCH-13: ask whether the shortlist's postings still exist. Spends no Firecrawl credits and
// makes no model calls, so there is no budget question and nothing to confirm.
export async function POST(req: Request): Promise<Response> {
  return forward(req, "/jobs/recheck");
}
