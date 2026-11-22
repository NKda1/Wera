import { forward } from "@/lib/server/agent";

/** 22 Nov: what the Upskills report is built from, as a fingerprint the page polls — it refreshes
 *  when a search's scores, a near miss read after it, or a decision moves it. */
export async function GET(req: Request) {
  return forward(req, "/upskills/version");
}
