import type { Metadata } from "next";
import { agent } from "@/lib/server/agent";
import { parseFilters } from "@/lib/upskills";
import { UpskillsBoard } from "./_components/UpskillsBoard";

export const metadata: Metadata = { title: "Upskills" };

// UPSK-01…06: what the jobs WERA scored for this person ask for against what their CV shows,
// worked out in the agent from the fit reports it already holds — no model call behind this page.
// Charts first (17 Nov): a long list of cards made the reader do the adding up; the page now does
// it and leads with the answer, and the detail is one table below.
//
// 20 Nov: the report is fetched here, on the server, for the window alone; both of its views (with
// the near misses and without) go down to `UpskillsBoard`, which applies the category, the status
// filter and "Only what I'm missing" in the browser, so those switch without a round trip. Each
// view carries its own chart data, so the charts move with the near-miss switch too. Keyed by the
// window, so a new window starts from what its URL says.
export default async function UpskillsPage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string; category?: string; near?: string; view?: string; family?: string }>;
}) {
  const filters = parseFilters(await searchParams);
  // The window and the kind of role are the agent's to count (21 Nov: UPSK-07); the rest is the page's.
  const report = await agent.upskills(filters.days, filters.family);
  return <UpskillsBoard key={`${filters.days}-${filters.family ?? "all"}`} report={report} initial={filters} />;
}
