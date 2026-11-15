import type { Metadata } from "next";
import { agent } from "@/lib/server/agent";
import { JobsScreen } from "./_components/JobsScreen";

export const metadata: Metadata = { title: "Today's shortlist" };

export default async function JobsPage({ searchParams }: { searchParams: Promise<{ show?: string }> }) {
  // LIFE-03: `?show=possibly-closed` lists the jobs nothing has shown open for a week.
  const possiblyClosedOnly = (await searchParams).show === "possibly-closed";
  const [list, meta, profile, sites, places, cv, suggestions] = await Promise.all([
    agent.jobList("new", possiblyClosedOnly ? "only" : undefined),
    agent.shortlistMeta(),
    agent.getProfile(),
    agent.sites(),
    agent.places(),
    agent.getCv(),
    // MATCH-13. Optional: the shortlist doesn't wait on, or fail for, what WERA learned.
    agent.learningSuggestions().catch(() => undefined),
  ]);
  const today = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long" }).format(new Date());
  return (
    <JobsScreen
      jobs={list.items}
      meta={meta}
      profile={profile}
      sites={sites}
      places={places}
      cvVersion={cv.version}
      today={today}
      possiblyClosed={list.possiblyClosed}
      showingPossiblyClosed={possiblyClosedOnly}
      suggestions={suggestions}
    />
  );
}
