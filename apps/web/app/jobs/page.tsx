import type { Metadata } from "next";
import { agent } from "@/lib/server/agent";
import { JobsScreen } from "./_components/JobsScreen";

export const metadata: Metadata = { title: "Today's shortlist" };

export default async function JobsPage() {
  const [jobs, meta, profile, sites, cv] = await Promise.all([
    agent.listJobs("new", "score"),
    agent.shortlistMeta(),
    agent.getProfile(),
    agent.sites(),
    agent.getCv(),
  ]);
  const today = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long" }).format(new Date());
  return <JobsScreen jobs={jobs} meta={meta} profile={profile} sites={sites} cvVersion={cv.version} today={today} />;
}
