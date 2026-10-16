import { forward } from "@/lib/server/agent";

/** PRF-05: queries and Firecrawl credits before a search runs. */
export async function GET(req: Request) {
  return forward(req, "/intake/estimate");
}
