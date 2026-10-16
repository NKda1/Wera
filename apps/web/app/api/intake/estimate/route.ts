import { handle } from "@/lib/server/http";
import { estimate } from "@/lib/server/store";
import type { IntakeChannel } from "@/lib/types";

const ALL: IntakeChannel[] = ["board_search", "email_alert", "watchlist"];

export async function GET(req: Request) {
  return handle(() => {
    const raw = new URL(req.url).searchParams.get("channels");
    const channels = raw ? (raw.split(",").filter((c) => ALL.includes(c as IntakeChannel)) as IntakeChannel[]) : ALL;
    return estimate(channels);
  });
}
