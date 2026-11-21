import { forward, seg } from "@/lib/server/agent";
import type { IdContext } from "@/lib/server/http";

/** ATSC-08: the job ad beside the CV, each keyword highlighted where the ATS check found it. */
export async function GET(req: Request, { params }: IdContext) {
  const { id } = await params;
  return forward(req, `/applications/${seg(id)}/compare`);
}
