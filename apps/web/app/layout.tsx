import type { Metadata, Viewport } from "next";
import { Instrument_Sans, JetBrains_Mono } from "next/font/google";
import { AppShell } from "@/lib/ui/AppShell";
import { THEME_SCRIPT } from "@/lib/ui/ThemeToggle";
import { ToastProvider } from "@/lib/ui/Toast";
import "./globals.css";

const instrument = Instrument_Sans({ subsets: ["latin"], variable: "--font-instrument", display: "swap" });
const jetbrains = JetBrains_Mono({ subsets: ["latin"], variable: "--font-jetbrains", display: "swap" });

export const metadata: Metadata = {
  title: { default: "WERA", template: "%s · WERA" },
  description: "Your master CV finds the roles, you approve the shortlist, WERA drafts the pack.",
};

export const viewport: Viewport = {
  // Next's defaults, plus `cover`: the page runs under an iPhone's rounded corners and home
  // indicator, and the bars pinned to the bottom (toasts, save bars) pad by `safe-area-inset-*`.
  // Zoom stays allowed.
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#131a16" },
  ],
};

// Every screen reads live demo state; nothing is prerendered.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `suppressHydrationWarning`: the theme script sets `class="dark"` before React hydrates.
    <html lang="en-GB" className={`${instrument.variable} ${jetbrains.variable}`} suppressHydrationWarning>
      <head>
        {/* Before first paint, so a dark page never flashes light. */}
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body>
        <ToastProvider>
          <AppShell>{children}</AppShell>
        </ToastProvider>
      </body>
    </html>
  );
}
