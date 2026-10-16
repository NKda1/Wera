import { handle, readJson } from "@/lib/server/http";
import { ImportSchema, parse } from "@/lib/server/schemas";
import { importJob } from "@/lib/server/store";

/** ATS-04: a URL or a pasted description. LinkedIn URLs are refused without any request. */
export async function POST(req: Request) {
  return handle(async () => importJob(parse(ImportSchema, await readJson(req))), { status: 201 });
}
