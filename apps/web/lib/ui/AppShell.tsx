import Link from "next/link";
import type { ReactNode } from "react";
import { agent } from "../server/agent";
import type { NavCounts } from "../types";
import { AppNav } from "./AppNav";
import { Logo, LogoMark } from "./Logo";

/** Sidebar on desktop, top bar under 1024 px (A11Y-02). In demo mode the badge is always visible (DEMO-04). */
export async function AppShell({ children }: { children: ReactNode }) {
  let counts: NavCounts | null = null;
  try {
    counts = await agent.navCounts();
  } catch {
    // The agent isn't reachable; pages explain it, the shell still renders.
  }
  const nav = counts ?? { jobs: 0, review: 0, demoMode: false };
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
          <AppNav counts={nav} layout="sidebar" />
          <div className="mt-auto px-1">
            {nav.demoMode ? <DemoBadge /> : null}
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
          {nav.demoMode ? <DemoBadge /> : null}
        </div>
        <AppNav counts={nav} layout="bar" />
      </header>

      <main id="main" className="min-w-0 px-4 pt-6 pb-24 sm:px-8 lg:px-12 lg:pt-10">
        {counts ? null : <AgentDown />}
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

function AgentDown() {
  return (
    <p role="alert" className="mb-6 rounded-[12px] border border-amber-100 bg-amber-50 px-4 py-3 text-[15px] text-amber-700">
      Can&apos;t reach the WERA agent service. Start WERA with <code className="font-mono text-[14px]">pnpm dev</code>, which runs the web app and the agent together.
    </p>
  );
}
