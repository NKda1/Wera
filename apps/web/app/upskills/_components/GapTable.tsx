"use client";

import { ArrowDownRight, ArrowUpRight, CircleAlert, Minus, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { upskillAnchor as anchor } from "@/lib/format";
import type { Upskill } from "@/lib/types";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import { gapNotes, mostlyPoor } from "@/lib/upskills";

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
 * a link to a row past the first ten opens the rest, so no link lands on nothing.
 *
 * 20 Nov: a click on a gap's bar in the chart above lands here too (`highlight`, with a click
 * count so the same bar clicked twice scrolls again): the row is opened if it is past the first
 * ten, scrolled to, marked and given focus, so a keyboard user lands on it as well. A gap asked
 * mostly by jobs the CV matched poorly says so — those weigh more in the order (`upskills.lift`).
 *
 * UPSK-08 (22 Nov): once the Upskills analyst has labelled a gap, its row says what would close it
 * (`evidence`), who mostly asks beside "Mostly asked by …" (`familyNote`), and the spellings it
 * merged in ("also written as …") — words only; every count and the order are the code's. */
export function GapTable({ missing, jobs, highlight }: { missing: Upskill[]; jobs: number; highlight?: { term: string; at: number } | null }) {
  const [all, setAll] = useState(false);
  useEffect(() => {
    const id = decodeURIComponent(window.location.hash.slice(1));
    if (!id) return;
    const at = missing.findIndex((u) => [u.term, ...u.spellings].map(anchor).includes(id));
    if (at >= FIRST) setAll(true);
    if (at >= 0) requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView());
  }, [missing]);
  useEffect(() => {
    if (!highlight) return;
    const at = missing.findIndex((u) => u.term === highlight.term);
    if (at < 0) return;
    if (at >= FIRST) setAll(true);
    requestAnimationFrame(() => {
      const row = document.getElementById(anchor(missing[at].term));
      row?.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      row?.focus({ preventScroll: true });
    });
  }, [highlight, missing]);

  if (!missing.length) return null;
  const rows = all ? missing : missing.slice(0, FIRST);
  return (
    <Card className="p-5" aria-labelledby="missing-title">
      <h2 id="missing-title" className="text-[16px] font-bold">
        Not on your CV yet
      </h2>
      <p className="mt-1 text-small text-ink-2">
        Most wanted first: how many jobs ask — on your list, and among the jobs you nearly matched when they&apos;re included — how often it&apos;s
        required, and the nearest thing you already have. A job your CV matched poorly counts more, up to twice: its gaps say most about what to
        improve.
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
            const notes = gapNotes(u);
            return (
              <tr
                key={u.term}
                id={own}
                tabIndex={-1}
                aria-current={highlight?.term === u.term ? "true" : undefined}
                className={cn("scroll-mt-20 border-t border-line align-top outline-none focus-visible:ring-2 focus-visible:ring-forest-700", highlight?.term === u.term && "bg-mint-100")}
              >
                <td className="py-2 pr-3">
                  {others.map((id) => (
                    <span key={id} id={id} className="block scroll-mt-20" aria-hidden="true" />
                  ))}
                  <span className="text-[14px] font-semibold text-ink [overflow-wrap:anywhere]">{u.term}</span>
                  {notes.also ? <span className="block text-ink-2">{notes.also}</span> : null}
                  {notes.evidence ? <span className="block text-ink">{notes.evidence}</span> : null}
                  {mostlyPoor(u) ? (
                    <span className="flex items-center gap-1 text-ink-2">
                      <CircleAlert className="size-3.5 shrink-0" aria-hidden="true" /> Mostly from jobs you matched poorly
                    </span>
                  ) : null}
                  {u.outsideField && u.families.length ? (
                    // UPSK-07 (21 Nov): asked mostly by another kind of role than the person's own.
                    <span className="block text-ink-2" title={u.families.map((f) => `${f.label}: ${f.jobs + f.nearMisses}`).join("\n")}>
                      Mostly asked by {u.families[0].label.toLowerCase()}
                      {notes.who ? ` · ${notes.who}` : null}
                    </span>
                  ) : notes.who ? (
                    <span className="block text-ink-2">{notes.who[0].toUpperCase() + notes.who.slice(1)}</span>
                  ) : null}
                  {u.closeTo.length ? <span className="block text-ink-2">Closest you have: {u.closeTo.join(", ")}</span> : null}
                  {u.examples.length ? (
                    <span className="block text-ink-2" title={u.examples.map((e) => `${e.title} at ${e.company}`).join("\n")}>
                      Asked by {u.examples.map((e) => e.company).join(", ")}
                    </span>
                  ) : null}
                  {u.nearMissExamples.length ? (
                    <span className="block text-ink-2">
                      Nearly matched: {u.nearMissExamples.map((e) => `${e.title} at ${e.company}`).join("; ")}
                    </span>
                  ) : null}
                  <span className="mt-0.5 flex items-center gap-1 text-ink-2 sm:hidden">
                    <trend.icon className="size-3.5" aria-hidden="true" /> {trend.label}
                  </span>
                </td>
                <td className="py-2 pr-3 tabular-nums">
                  <span className="block whitespace-nowrap">
                    <span className="font-semibold text-ink">{u.jobs}</span>
                    <span className="text-ink-2">
                      {" "}
                      {u.nearMisses ? `job${u.jobs === 1 ? "" : "s"} on your list` : `of ${jobs} · ${Math.round(u.share * 100)}%`}
                    </span>
                  </span>
                  {u.nearMisses ? (
                    <span className="block whitespace-nowrap text-ink-2">
                      · <span className="font-semibold text-ink">{u.nearMisses}</span> you nearly matched
                    </span>
                  ) : null}
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
