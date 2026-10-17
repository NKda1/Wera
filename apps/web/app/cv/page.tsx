import type { Metadata } from "next";
import { agent } from "@/lib/server/agent";
import { CvEditor } from "./CvEditor";

export const metadata: Metadata = { title: "Master CV" };

export default async function CvPage() {
  const [cv, contact] = await Promise.all([agent.getCv(), agent.getContact()]);
  return <CvEditor initialCv={cv} initialContact={contact} />;
}
