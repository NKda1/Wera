import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** Learn about the company: its post and its own About page (robots.txt permitting), each item quoted and checked. */
export async function POST(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/company-brief`);
}
