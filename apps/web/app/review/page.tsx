import type { Metadata } from "next";
import { listApplications } from "@/lib/server/store";
import { ReviewList } from "./_components/ReviewList";

export const metadata: Metadata = { title: "Review" };

export default function ReviewPage() {
  const apps = listApplications().filter((a) => ["drafting", "changes_requested", "ready_for_review", "approved"].includes(a.status));
  return <ReviewList apps={apps} />;
}
