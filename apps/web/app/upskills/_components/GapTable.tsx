"use client";

import { ArrowDownRight, ArrowUpRight, Minus, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { upskillAnchor as anchor } from "@/lib/format";
import type { Upskill } from "@/lib/types";
import { Card } from "@/lib/ui/Card";

// Rows shown before "Show all": the gaps worth acting on this week, not an endless scroll.
const FIRST = 10;

const TREND: Record<Upskill["trend"], { label: string; icon: typeof Minus }> = {
  new: { label: "New lately", icon: Sparkles },
  rising: { label: "Rising", icon: ArrowUpRight },
  steady: { label: "Steady", icon: Minus },
  falling: { label: "Falling", icon: ArrowDownRight },
};

/** UPSK-02: what the CV doesn't show yet, as one table — most wanted first.
 *
 * Each row keeps the anchors a pack's "Missing" links land on (every spelling the posts used), and
 * a link to a row past the first ten opens the rest, so no link lands on nothing. */
export function GapTable({ missing, jobs }: { missing: Upskill[]; jobs: number }) {
  const [all, setAll] = useState(false);
  useEffect(() => {
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (!id) return;
    const at = missing.findIndex((u) => [u.term, ...u.spellings].map(anchor).includes(id));
    if (at >= FIRST) setAll(true);
    if (at >= 0) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView());
  }, [missing]);

  if (!missing.length) return null;
  const rows = all ? missing : missing.slice(0, FIRST);
  return (
    <Card className="p-5" aria-labelledby="missing-title">
      <h2 id="missing-title" className="text-[16px] font-bold">
        Not on your CV yet
      </h2>
      <p className="mt-1 text-small text-ink-2">
        Most wanted first: how many jobs ask, how often it&apos;s required, and the nearest thing you already have.
      </p>
      <table className="mt-3 w-full text-small">
        <thead className="text-left text-ink-2">
          <tr>
            <th className="py-1.5 pr-3 font-semibold">Skill</th>
            <th className="py-1.5 pr-3 font-semibold">Jobs asking</th>
            <th className="py-1.5 pr-3 font-semibold">Required</th>
            <th className="hidden py-1.5 font-semibold sm:table-cell">Trend</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((u) => {
            const own = anchor(u.term);
            const others = u.spellings.map(anchor).filter((id, i, list) => id !== own && list.indexOf(id) === i);
            const trend = TREND[u.trend];
            return (
              <tr key={u.term} id={own} className="scroll-mt-20 border-t border-line align-top">
                <td className="py-2 pr-3">
                  {others.map((id) => (
                    <span key={id} id={id} className="block scroll-mt-20" aria-hidden="true" />
                  ))}
                  <span className="text-[14px] font-semibold text-ink [overflow-wrap:anywhere]">{u.term}</span>
                  {u.closeTo.length ? <span className="block text-ink-2">Closest you have: {u.closeTo.join(", ")}</span> : null}
                  {u.examples.length ? (
                    <span className="block text-ink-2" title={u.examples.map((e) => `${e.title} at ${e.company}`).join("\n")}>
                      Asked by {u.examples.map((e) => e.company).join(", ")}
                    </span>
                  ) : null}
                  <span className="mt-0.5 flex items-center gap-1 text-ink-2 sm:hidden">
                    <trend.icon className="size-3.5" aria-hidden="true" /> {trend.label}
                  </span>
                </td>
                <td className="py-2 pr-3 whitespace-nowrap tabular-nums">
                  <span className="font-semibold text-ink">{u.jobs}</span>
                  <span className="text-ink-2"> of {jobs} · {Math.round(u.share * 100)}%</span>
                </td>
                <td className="py-2 pr-3 tabular-nums">{Math.round(u.requiredShare * 100)}%</td>
                <td className="hidden py-2 sm:table-cell">
                  <span className="flex items-center gap-1 whitespace-nowrap text-ink-2">
                    <trend.icon className="size-3.5" aria-hidden="true" /> {trend.label}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {missing.length > FIRST ? (
        <button type="button" onClick={() => setAll((a) => !a)} className="mt-3 text-small font-semibold text-forest-700 hover:underline">
          {all ? `Show the top ${FIRST}` : `Show all ${missing.length}`}
        </button>
      ) : null}
    </Card>
  );
}
