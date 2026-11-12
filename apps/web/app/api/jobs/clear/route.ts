import { forward } from "@/lib/server/agent";

// SRCH-27: the testing control that empties the job list. GET is what the confirm names before
// anything goes; POST does it. Both answer 403 unless `allow_clear_jobs` is on in config.
export async function GET(req: Request): Promise<Response> {
  return forward(req, "/jobs/clear");
}

export async function POST(req: Request): Promise<Response> {
  return forward(req, "/jobs/clear");
}
