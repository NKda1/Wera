import { handle, readJson } from "@/lib/server/http";
import { parse, ProfileSchema } from "@/lib/server/schemas";
import { getProfile, putProfile } from "@/lib/server/store";
import type { SearchProfile } from "@/lib/types";

export async function GET() {
  return handle(() => getProfile());
}

export async function PUT(req: Request) {
  return handle(async () => putProfile(parse(ProfileSchema, await readJson(req)) as SearchProfile));
}
