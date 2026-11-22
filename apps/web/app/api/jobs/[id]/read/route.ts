import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** 21 Nov: "Read the full ad" on a preview — one lookup for the employer's own posting, the read
 *  ladder, then a score that counts towards the day's limit. Never the job API's tracked link. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/jobs/${seg(id)}/read`);
}
