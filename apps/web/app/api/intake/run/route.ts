import { ApiError, handle, readJson } from "@/lib/server/http";
import { startRuns } from "@/lib/server/store";
import type { IntakeChannel } from "@/lib/types";

const ALL: IntakeChannel[] = ["board_search", "email_alert", "watchlist"];

export async function POST(req: Request) {
  return handle(async () => {
    const body = await readJson<{ channels?: string[] }>(req);
    const channels = (body.channels ?? ALL).filter((c): c is IntakeChannel => ALL.includes(c as IntakeChannel));
    if (!channels.length) throw new ApiError(422, "VALIDATION", "Choose at least one channel.");
    return startRuns(channels);
  });
}
