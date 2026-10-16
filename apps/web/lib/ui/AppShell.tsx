import Link from "next/link";
import type { ReactNode } from "react";
import { navCounts } from "../server/store";
import { AppNav } from "./AppNav";
import { Logo, LogoMark } from "./Logo";

/** Sidebar on desktop, top bar under 1024 px (A11Y-02). The demo banner is always visible (DEMO-04). */
export function AppShell({ children }: { children: ReactNode }) {
  const counts = navCounts();
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[240px_1fr]">
      <a href="#main" className="sr-only z-50 rounded-control bg-forest-900 px-4 py-2 text-white focus:not-sr-only focus:fixed focus:top-3 focus:left-3">
        Skip to content
      </a>

      <aside className="hidden border-r border-line bg-white lg:block">
        <div className="sticky top-0 flex h-dvh flex-col px-4 py-6">
          <Link href="/jobs" className="mb-8 rounded-control px-1" aria-label="WERA home">
            <Logo />
          </Link>
          <AppNav counts={counts} layout="sidebar" />
          <div className="mt-auto px-1">
            <DemoBadge />
            <p className="mt-2 text-small text-ink-2">Self-hosted · v0.1</p>
          </div>
        </div>
      </aside>

      <header className="sticky top-0 z-20 border-b border-line bg-white/95 px-4 pt-3 pb-2 backdrop-blur lg:hidden">
        <div className="mb-2 flex items-center justify-between">
          <Link href="/jobs" className="flex items-center gap-2" aria-label="WERA home">
            <LogoMark className="size-8" />
            <span className="text-[19px] font-bold tracking-[0.02em] text-forest-900">WERA</span>
          </Link>
          <DemoBadge />
        </div>
        <AppNav counts={counts} layout="bar" />
      </header>

      <main id="main" className="min-w-0 px-4 pt-6 pb-24 sm:px-8 lg:px-12 lg:pt-10">
        {children}
      </main>
    </div>
  );
}

function DemoBadge() {
  return (
    <span className="inline-flex items-center rounded-full bg-amber-50 px-2.5 py-1 text-small font-semibold text-amber-700" title="Sample jobs and a mock model. No keys needed, nothing is spent.">
      Demo mode
    </span>
  );
}
