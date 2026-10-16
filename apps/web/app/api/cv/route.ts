import { handle, readJson } from "@/lib/server/http";
import { getCv, putCv } from "@/lib/server/store";

/** The master CV without contact details (those are fetched separately; CV-06). */
export async function GET() {
  return handle(() => getCv());
}

export async function PUT(req: Request) {
  return handle(async () => putCv(await readJson(req)));
}
