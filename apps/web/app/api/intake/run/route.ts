import { forward } from "@/lib/server/agent";

/** Starts one run per enabled channel; 409 while a run is active. */
export async function POST(req: Request) {
  return forward(req, "/intake/run");
}
