"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { NavCounts } from "../types";
import { cn } from "./cn";

const items = [
  { href: "/jobs", label: "Jobs", count: "jobs" as const },
  { href: "/review", label: "Review", count: "review" as const },
  { href: "/cv", label: "Master CV" },
  { href: "/tracker", label: "Tracker" },
  { href: "/settings", label: "Settings" },
];

export function AppNav({ counts, layout }: { counts: NavCounts; layout: "sidebar" | "bar" }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Main">
      <ul className={cn(layout === "sidebar" ? "flex flex-col gap-1" : "scrollbar-none -mx-4 flex gap-1 overflow-x-auto px-4")}>
        {items.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          const n = item.count ? counts[item.count] : 0;
          return (
            <li key={item.href} className="shrink-0">
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-11 items-center justify-between gap-3 rounded-control px-3 text-[15px] transition-colors",
                  active ? "bg-mint-100 font-semibold text-forest-900" : "text-ink hover:bg-mint-50",
                )}
              >
                {item.label}
                {item.count && n > 0 ? (
                  <span
                    className={cn(
                      "min-w-6 rounded-full px-1.5 py-0.5 text-center text-[12px] font-semibold tabular-nums",
                      active ? "bg-forest-900 text-white" : "bg-mint-100 text-forest-900",
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
