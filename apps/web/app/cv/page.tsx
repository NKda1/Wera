import type { Metadata } from "next";
import { getContact, getCv } from "@/lib/server/store";
import { CvEditor } from "./CvEditor";

export const metadata: Metadata = { title: "Master CV" };

export default function CvPage() {
  return <CvEditor initialCv={getCv()} initialContact={getContact()} />;
}
