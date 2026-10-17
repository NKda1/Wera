import type { Metadata } from "next";
import { agent } from "@/lib/server/agent";
import { SettingsScreen } from "./SettingsScreen";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  return <SettingsScreen initial={await agent.getSettings()} />;
}
