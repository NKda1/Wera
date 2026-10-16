import { handle, readJson } from "@/lib/server/http";
import { parse, SettingsPatchSchema } from "@/lib/server/schemas";
import { getSettings, putSettings } from "@/lib/server/store";

/** Non-secret settings plus key status (set / missing). Key values are never read here (SET-01, SET-07). */
export async function GET() {
  return handle(() => getSettings());
}

export async function PUT(req: Request) {
  return handle(async () => putSettings(parse(SettingsPatchSchema, await readJson(req))));
}
