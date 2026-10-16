import type { Metadata } from "next";
import { listApplications } from "@/lib/server/store";
import { TrackerBoard } from "./TrackerBoard";

export const metadata: Metadata = { title: "Tracker" };

export default function TrackerPage() {
  return <TrackerBoard apps={listApplications().filter((a) => a.status !== "discarded")} />;
}
