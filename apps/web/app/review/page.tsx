import type { Metadata } from "next";
import { agent } from "@/lib/server/agent";
import { ReviewList } from "./_components/ReviewList";

export const metadata: Metadata = { title: "Review" };

export default async function ReviewPage() {
  const apps = (await agent.listApplications()).filter((a) => ["drafting", "changes_requested", "ready_for_review", "approved"].includes(a.status));
  return <ReviewList apps={apps} />;
}
