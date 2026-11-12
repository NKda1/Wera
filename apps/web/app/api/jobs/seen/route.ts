import { forward } from "@/lib/server/agent";

/** SRCH-25: "Mark all seen" — every job tagged New loses the tag. */
export async function POST(req: Request): Promise<Response> {
  return forward(req, "/jobs/seen");
}
