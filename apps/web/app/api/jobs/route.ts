import { handle } from "@/lib/server/http";
import { listJobs } from "@/lib/server/store";
import type { JobStatus } from "@/lib/types";

const STATUSES: JobStatus[] = ["new", "shortlisted", "in_application", "snoozed", "rejected", "hidden", "closed", "archived"];

export async function GET(req: Request) {
  return handle(() => {
    const q = new URL(req.url).searchParams;
    const status = STATUSES.find((s) => s === q.get("status")) ?? "new";
    const sort = q.get("sort") === "date" ? "date" : "score";
    const min = Number(q.get("minScore") ?? 0);
    const channel = q.get("channel");
    const location = q.get("location")?.toLowerCase();
    const remote = q.get("remote");
    const items = listJobs({ status, sort }).filter(
      (j) =>
        j.fit.total >= min &&
        (!channel || j.sources.some((s) => s.channel === channel)) &&
        (!location || j.location.raw.toLowerCase().includes(location)) &&
        (!remote || j.remote === remote),
    );
    return { items, next: null };
  });
}
