import type { Metadata } from "next";
import { agent } from "@/lib/server/agent";
import { TrackerBoard } from "./TrackerBoard";

export const metadata: Metadata = { title: "Tracker" };

export default async function TrackerPage() {
  return <TrackerBoard apps={(await agent.listApplications()).filter((a) => a.status !== "discarded")} />;
}
