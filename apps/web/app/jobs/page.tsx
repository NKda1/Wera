import type { Metadata } from "next";
import { SITES } from "@/lib/demo/cv";
import { getProfile, listJobs, shortlistMeta } from "@/lib/server/store";
import { JobsScreen } from "./_components/JobsScreen";

export const metadata: Metadata = { title: "Today's shortlist" };

export default function JobsPage() {
  const jobs = listJobs({ status: "new", sort: "score" });
  const meta = shortlistMeta();
  const today = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long" }).format(new Date());
  return (
    <JobsScreen
      jobs={jobs}
      meta={meta}
      profile={getProfile()}
      sites={SITES.map(({ domain, name, termsReviewedOn }) => ({ domain, name, reviewed: termsReviewedOn != null }))}
      today={today}
    />
  );
}
