import { forward } from "@/lib/server/agent";

/** ATS-04: a URL or a pasted description. LinkedIn URLs are refused without any request. */
export async function POST(req: Request) {
  return forward(req, "/jobs/import");
}
