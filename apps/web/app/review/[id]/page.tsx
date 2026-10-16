import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ApiError } from "@/lib/server/http";
import { cvLines, getApplication, getCv, getJob } from "@/lib/server/store";
import { ReviewScreen } from "../_components/ReviewScreen";

export const metadata: Metadata = { title: "Review pack" };

const VENDORS = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby" } as const;

export default async function ReviewPackPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let app;
  try {
    app = getApplication(id);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    throw err;
  }
  const job = getJob(app.jobId);
  return <ReviewScreen initial={app} cv={getCv()} lines={cvLines()} vendor={job.ats ? VENDORS[job.ats.vendor] : undefined} />;
}
