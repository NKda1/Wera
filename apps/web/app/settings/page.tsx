import type { Metadata } from "next";
import { getSettings } from "@/lib/server/store";
import { SettingsScreen } from "./SettingsScreen";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return <SettingsScreen initial={getSettings()} />;
}
