"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import type { NavCounts } from "../types";
import { cn } from "./cn";
import { revealInline, useScrollEdges } from "./scrollEdges";

const items = [
  { href: "/jobs", label: "Jobs", count: "jobs" as const },
  { href: "/review", label: "Review", count: "review" as const },
  { href: "/cv", label: "Master CV" },
  { href: "/upskills", label: "Upskills" },
  { href: "/tracker", label: "Tracker" },
  { href: "/settings", label: "Settings" },
];

export function AppNav({ counts, layout }: { counts: NavCounts; layout: "sidebar" | "bar" }) {
  const pathname = usePathname();
  const bar = layout === "bar";
  const list = useRef<HTMLUListElement>(null);
  const fade = useScrollEdges(list, `${counts.jobs}|${counts.review}`);
  // In the phone bar six sections don't fit, and Tracker or Settings used to open with their own
  // tab scrolled out of sight. The current one is always brought into view.
  useEffect(() => {
    if (bar) revealInline(list.current, list.current?.querySelector<HTMLElement>('[aria-current="page"]') ?? null);
  }, [bar, pathname]);
  return (
    <nav aria-label="Main" className={cn(bar && "min-w-0 flex-1")}>
      <ul ref={list} style={bar ? fade : undefined} className={cn(bar ? "scrollbar-none -mr-4 flex gap-1 overflow-x-auto pr-4" : "flex flex-col gap-1")}>
        {items.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const n = item.count ? counts[item.count] : 0;
          return (
            <li key={item.href} className="shrink-0">
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-11 items-center justify-between gap-3 rounded-control px-3 text-[15px] transition-colors duration-150",
                  // A hairline of accent marks where you are — down the left in the sidebar, along
                  // the bottom in the bar, where a left edge reads as a divider between tabs. The
                  // rest stays quiet until hovered.
                  active
                    ? cn("bg-mint-100 font-semibold text-forest-900", bar ? "shadow-[inset_0_-2px_0_var(--color-forest-700)]" : "shadow-[inset_2px_0_0_var(--color-forest-700)]")
                    : "text-ink-2 hover:bg-mint-50 hover:text-ink",
                )}
              >
                {item.label}
                {item.count && n > 0 ? (
                  <span
                    className={cn(
                      "min-w-6 rounded-none px-1.5 py-0.5 text-center text-[12px] font-semibold tabular-nums",
                      active ? "bg-accent-strong text-white" : "bg-mint-100 text-forest-900",
                    )}
                  >
                    {n}
                    <span className="sr-only"> {item.count === "jobs" ? "jobs waiting" : "packs to review"}</span>
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
