import { forward } from "@/lib/server/agent";

/** PRF-01, PRF-04: proposes a profile from the master CV and returns the diff. Saves nothing. */
export async function POST(req: Request) {
  return forward(req, "/profile/generate");
}
