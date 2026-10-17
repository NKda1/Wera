import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { agent } from "@/lib/server/agent";
import { ApiError } from "@/lib/server/http";
import { ReviewScreen } from "../_components/ReviewScreen";

export const metadata: Metadata = { title: "Review pack" };

const VENDORS = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby" } as const;

export default async function ReviewPackPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let app;
  try {
    app = await agent.getApplication(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }
  const [job, cv, lines] = await Promise.all([agent.getJob(app.jobId), agent.getCv(), agent.cvLines()]);
  return <ReviewScreen initial={app} cv={cv} lines={lines} vendor={job.ats ? VENDORS[job.ats.vendor] : undefined} />;
}
