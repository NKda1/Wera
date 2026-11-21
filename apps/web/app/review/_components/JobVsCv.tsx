"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AtsCompare, AtsReport } from "@/lib/types";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import { compareStats, matchBand, segments, type Mark } from "./highlight";

// The three marks, styled apart by fill *and* underline, so they read without colour too.
const MARK: Record<Mark, { label: string; className: string }> = {
  matched: { label: "On your CV", className: "bg-mint-100 text-forest-900 underline decoration-forest-700 decoration-2 underline-offset-2" },
  in_cv: { label: "In your master CV, not this pack", className: "bg-amber-50 text-amber-700 underline decoration-amber-500 decoration-dashed decoration-2 underline-offset-2" },
  missing: { label: "Not in your CV", className: "bg-red-50 text-red-700 underline decoration-red-700 decoration-dotted decoration-2 underline-offset-2" },
};

const BAND = { good: "text-forest-700", fair: "text-amber-700", poor: "text-red-700" } as const;
const BAR = { good: "bg-accent", fair: "bg-amber-500", poor: "bg-red-700" } as const;

/**
 * ATSC-08 (20 Nov): the job ad beside the CV, after resume-matcher's `jd-comparison-view`
 * (Apache-2.0, see NOTICE). Each of the post's keywords is highlighted where it is, on both
 * pages, by the matcher the ATS card counts with — so a word green here is a word the card
 * counts as matched. Two columns where there is room, the ad above the CV on a phone.
 */
export function JobVsCv({ appId, ats }: { appId: string; ats: AtsReport }) {
  const [state, setState] = useState<{ view: AtsCompare } | { error: string } | null>(null);

  useEffect(() => {
    let live = true;
    setState(null);
    api.getCompare(appId).then(
      (view) => live && setState({ view }),
      (e: unknown) => live && setState({ error: e instanceof Error ? e.message : "Couldn't load the comparison." }),
    );
    return () => {
      live = false;
    };
    // An edit re-measures the ATS report, and the CV text with it.
  }, [appId, ats]);

  if (state === null) return <Card className="p-5 text-small text-ink-2">Reading the job ad against your CV…</Card>;
  if ("error" in state) return <Card className="p-5 text-small text-amber-700">Couldn&apos;t compare the job ad with your CV: {state.error}</Card>;

  const { view } = state;
  const stats = compareStats(view);
  const band = matchBand(stats.percent);
  return (
    <div className="flex flex-col gap-4">
      <Card className="p-5" aria-label="Keyword match">
        <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
          <dl className="flex flex-wrap gap-x-6 gap-y-2 text-[14px]">
            <Stat label="Keywords" value={stats.total} />
            <Stat label="On your CV" value={stats.matched} />
            <Stat label="Could add from your CV" value={stats.inCv} />
            <Stat label="Need new experience" value={stats.missing} />
          </dl>
          <p className={cn("text-[34px] leading-none font-bold tabular-nums", BAND[band])}>
            {stats.percent}%<span className="ml-1.5 text-[14px] font-semibold text-ink-2">match</span>
          </p>
        </div>
        <div className="mt-3 h-2 overflow-hidden rounded-none bg-mint-100" role="progressbar" aria-label="Keyword match" aria-valuemin={0} aria-valuemax={100} aria-valuenow={stats.percent}>
          <div className={cn("h-full rounded-none", BAR[band])} style={{ width: `${stats.percent}%` }} />
        </div>
        <p className="mt-2 text-small text-ink-2">
          Weighted as the ATS card weighs it: a required keyword counts twice.
          {stats.potential > stats.percent ? ` Adding what your master CV already shows would make it ${stats.potential}%.` : ""}
          {stats.missing ? (
            <>
              {" "}
              For the rest, <Link href="/upskills" className="underline hover:text-forest-700">see what to learn</Link>.
            </>
          ) : null}
        </p>
        {view.stale ? (
          <p className="mt-2 bg-amber-50 px-3 py-2 text-small text-amber-700">
            Measured again just now: WERA has learnt new spellings since this pack&apos;s ATS card was measured. The card catches up when you next edit the pack.
          </p>
        ) : null}
        <ul aria-label="Colour key" className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-small text-ink-2">
          {(Object.keys(MARK) as Mark[]).map((m) => (
            <li key={m} className="flex items-center gap-1.5">
              <span className={cn("px-1.5", MARK[m].className)}>abc</span>
              {MARK[m].label}
            </li>
          ))}
        </ul>
      </Card>

      <div className="grid items-start gap-4 @4xl:grid-cols-2">
        <Page title="Job ad" note={view.descriptionStatus === "full" ? undefined : "Only part of the post could be read, so some keywords may be missing from it."} text={view.jobText} view={view} side="job" />
        <Page title="Your CV" note="What the CV file prints, as a parser reads it." text={view.cvText} view={view} side="cv" />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-small text-ink-2">{label}</dt>
      <dd className="text-[20px] font-bold tabular-nums text-forest-900">{value}</dd>
    </div>
  );
}

function Page({ title, note, text, view, side }: { title: string; note?: string; text: string; view: AtsCompare; side: "job" | "cv" }) {
  return (
    <Card className="min-w-0 p-5">
      <h2 className="text-[16px] font-bold">{title}</h2>
      {note ? <p className="mt-0.5 text-small text-ink-2">{note}</p> : null}
      {text ? (
        <p className="mt-3 text-[14px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
          {segments(text, view.keywords, side).map((s, i) =>
            s.mark ? (
              <mark key={i} title={`${s.term} · ${MARK[s.mark].label}`} className={cn("px-0.5", MARK[s.mark].className)}>
                {s.text}
              </mark>
            ) : (
              <span key={i}>{s.text}</span>
            ),
          )}
        </p>
      ) : (
        <p className="mt-3 text-small text-ink-2">{side === "job" ? "This post has no description to show." : "The CV has no text yet."}</p>
      )}
    </Card>
  );
}
