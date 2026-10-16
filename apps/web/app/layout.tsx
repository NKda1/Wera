import type { Metadata, Viewport } from "next";
import { Instrument_Sans, JetBrains_Mono } from "next/font/google";
import { AppShell } from "@/lib/ui/AppShell";
import { ToastProvider } from "@/lib/ui/Toast";
import "./globals.css";

const instrument = Instrument_Sans({ subsets: ["latin"], variable: "--font-instrument", display: "swap" });
const jetbrains = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains", display: "swap" });

export const metadata: Metadata = {
  title: { default: "WERA", template: "%s · WERA" },
  description: "Your master CV finds the roles, you approve the shortlist, WERA drafts the pack.",
};

export const viewport: Viewport = { themeColor: "#146C43" };

// Every screen reads live demo state; nothing is prerendered.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-GB" className={`${instrument.variable} ${jetbrains.variable}`}>
      <body>
        <ToastProvider>
          <AppShell>{children}</AppShell>
        </ToastProvider>
      </body>
    </html>
  );
}
