"use client";

import { ChevronDown, ExternalLink } from "lucide-react";
import { useId, useState } from "react";
import {
  formatAge,
  formatAgo,
  formatClosing,
  formatEvergreen,
  formatInternational,
  formatLiveness,
  formatLocation,
  formatOrigin,
  formatPostedAge,
  formatSalary,
  sourceLine,
  sourceName,
} from "@/lib/format";
import { api } from "@/lib/api";
import type { ScoredJob } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { Chip } from "@/lib/ui/Chip";
import { cn } from "@/lib/ui/cn";
import { JobDescription, JobSummaryView } from "@/lib/ui/JobDescription";
import { Markdown } from "@/lib/ui/Markdown";
import { Menu } from "@/lib/ui/Menu";
import { Pill } from "@/lib/ui/Pill";
import { ScoreRing } from "@/lib/ui/ScoreRing";
import { SubScoreBar } from "@/lib/ui/SubScoreBar";

/** Where a posting date came from, which is what decides whether to trust it to the day. */
const POSTED_SOURCE: Record<ScoredJob["postedPrecision"], string> = {
  exact: "the employer's own job board, so it's exact",
  approximate: "a board or alert that said how long ago, so it's right to about a day",
  unknown: "not stated by the source, so this counts as stale",
};

const SUBSCORES = [
  ["core", "Core stack"],
  ["adjacent", "Adjacent"],
  ["experience", "Experience"],
  ["eligibility", "Eligibility"],
] as const;

export function JobCard({
  job,
  rank,
  isNew,
  busy,
  onOpen,
  onApprove,
  onSnooze,
  onReject,
}: {
  job: ScoredJob;
  rank: number;
  isNew: boolean;
  busy: boolean;
  /** Called when the details are opened: that is what "seen" means (SRCH-25). */
  onOpen?: () => void;
  onApprove: () => void;
  onSnooze: (days: 1 | 3 | 7) => void;
  onReject: () => void;
}) {
  const [open, setOpen] = useState(false);
  const origin = formatOrigin(job);
  const detailsId = useId();
  const { fit } = job;
  // MATCH-10: appended rather than replacing anything, and empty for a confirmed job — if every
  // live card carried a liveness note, the one job WERA couldn't confirm would be invisible.
  const liveness = formatLiveness(job);
  const meta = [
    // 20 Nov: an agency's repost reads under the employer it names, with the agency beside it.
    job.postedBy ? `${job.company} (via ${job.postedBy})` : job.company,
    formatLocation(job),
    formatInternational(job),
    formatSalary(job.salary),
    formatPostedAge(job),
    formatClosing(job),
    liveness,
    formatEvergreen(job),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Card
      className={cn("animate-fade-in p-5 transition-colors duration-150 hover:border-forest-700/50 @2xl:p-7", isNew && "border-l-2 border-l-forest-700")}
      aria-labelledby={`${detailsId}-title`}
    >
      {/* Laid out by the jobs screen's width (`@container` in JobsScreen), not the window's: the
          sidebar takes 368px at 1024, and keyed to `md:` the description got ~230px there. */}
      <div className="grid grid-cols-[64px_1fr] gap-x-4 gap-y-4 @xl:gap-x-5 @2xl:grid-cols-[64px_1fr_112px]">
        <div className="flex flex-col items-center gap-1.5">
          <ScoreRing score={fit.total} roughOnly={fit.roughOnly} />
          <span className="text-small text-ink-2">fit · #{rank}</span>
        </div>

        <div className="min-w-0">
          {/* SRCH-25: a filled tag ahead of the title, in words, so it reads in either theme and to
              a screen reader — the old one was a pale pill after the title that every run wiped.
              The origin stays after the tag has gone: where and when a job was found is still
              worth knowing once it has been read. */}
          {isNew || origin ? (
            <p className="mb-1.5 flex flex-wrap items-center gap-2">
              {isNew ? (
                <span className="rounded-none bg-accent px-2.5 py-0.5 text-[11px] font-bold tracking-wide text-white uppercase">New</span>
              ) : null}
              {/* SRCH-25 (17 Nov): which search, to the minute, and how long ago — so a job from
                  this morning's search reads apart from one a week old. */}
              {origin ? (
                <span className="text-small text-ink-2">
                  {origin}
                  {job.foundBy ? <> · {formatAgo(job.foundBy.at)}</> : null}
                </span>
              ) : null}
              {/* SRCH-32: Adzuna's terms ask for its name, linked, on every job it found, at
                  least 116×23 px. WERA never follows Adzuna's tracked link to the job itself. */}
              {job.via === "adzuna" ? (
                <a
                  href="https://www.adzuna.co.uk"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex h-[23px] min-w-[116px] items-center justify-center rounded-none border border-ink-2/30 px-2.5 text-[12px] font-semibold text-ink hover:text-forest-700"
                >
                  Jobs by Adzuna
                </a>
              ) : null}
            </p>
          ) : null}
          <h3 id={`${detailsId}-title`} className="text-h2">
            <button
              type="button"
              onClick={() => {
                if (!open) onOpen?.();
                setOpen((o) => !o);
              }}
              aria-expanded={open}
              aria-controls={detailsId}
              className="group -mx-1 inline-flex items-start gap-1.5 rounded-none px-1 text-left hover:text-forest-700 pointer-coarse:-my-2 pointer-coarse:py-2"
            >
              {job.title}
              <ChevronDown className={cn("mt-1.5 size-4 shrink-0 text-ink-2 transition-transform group-hover:text-forest-700", open && "rotate-180")} aria-hidden="true" />
              <span className="sr-only">{open ? "Hide details" : "Show details"}</span>
            </button>
            {/* CV-08: this score judged a CV, a model or a posting that has since changed. Amber,
                not red — the score is out of date, not wrong, and it is still the best evidence
                WERA has until the re-scoring pass reaches this job. */}
            {fit.stale ? (
              <Pill tone="amber" dot={false} className="ml-2 align-middle text-[11px]">
                Re-scoring
              </Pill>
            ) : null}
          </h3>
          <p className="mt-0.5 text-small text-ink-2">{meta}</p>
          <p className="mt-1.5">
            <span className="inline-flex rounded-none bg-mint-100 px-2.5 py-0.5 text-[12px] font-semibold text-forest-900 sm:rounded-none">{sourceLine(job)}</span>
          </p>
        </div>

        <div className="col-span-2 grid gap-x-8 gap-y-1.5 @xl:grid-cols-2 @2xl:col-span-1 @2xl:col-start-2">
          {SUBSCORES.map(([key, label]) => (
            <SubScoreBar key={key} label={label} score={fit[key].score} />
          ))}
        </div>

        <div className="col-span-2 rounded-control bg-mint-50 px-3.5 py-2.5 text-[14px] leading-relaxed @2xl:col-span-1 @2xl:col-start-2">
          {fit.roughOnly ? (
            <p>Only the title, company and location are known. Open it on LinkedIn to read the description; the score is a rough title match.</p>
          ) : (
            <p>
              <span>“{fit.jobQuote}”</span> — {fit.reason}
              {/* The CV lines behind the verdict are traceability, not reading: shown with the details. */}
              {open && fit.cvBulletIds.length ? (
                <span className="ml-1.5 inline-flex flex-wrap gap-1 align-middle">
                  {fit.cvBulletIds.map((id) => (
                    <Chip key={id} variant="source">
                      {id}
                    </Chip>
                  ))}
                </span>
              ) : null}
            </p>
          )}
          {/* MATCH-12: why it sits where it does when the person's decisions moved it. The score
              ring is the fit and stays it; this explains only the order. */}
          {job.learned ? <p className="mt-1.5 text-small text-ink-2">{job.learned.reason}.</p> : null}
        </div>

        {/* One row on a phone, with tighter padding there: at the buttons' usual padding the three
            needed ~291px, and a 360px phone gives the card 288. Kept as a row rather than stacked
            so Snooze's menu, which opens leftwards from its right edge, stays on screen. */}
        <div className="col-span-2 flex gap-2 @2xl:col-span-1 @2xl:col-start-3 @2xl:row-span-3 @2xl:row-start-1 @2xl:flex-col">
          <Button onClick={onApprove} disabled={busy} className="flex-1 @max-xl:px-3 @2xl:flex-none">
            Approve
          </Button>
          <Menu
            className="flex-1 @2xl:flex-none"
            label={`Snooze ${job.title}`}
            items={[
              { label: "1 day", onSelect: () => onSnooze(1) },
              { label: "3 days", onSelect: () => onSnooze(3) },
              { label: "7 days", onSelect: () => onSnooze(7) },
            ]}
            trigger={({ open: menuOpen, toggle, id }) => (
              <Button variant="secondary" onClick={toggle} disabled={busy} aria-haspopup="menu" aria-expanded={menuOpen} aria-controls={menuOpen ? id : undefined} className="w-full @max-xl:px-3">
                Snooze
              </Button>
            )}
          />
          <Button variant="ghost" onClick={onReject} disabled={busy} className="flex-1 @max-xl:px-3 @2xl:flex-none">
            Reject
          </Button>
        </div>
      </div>

      {open ? (
        <div id={detailsId} className="mt-5 grid gap-6 border-t border-line pt-5 @4xl:grid-cols-[1fr_260px] @4xl:pl-[84px]">
          <div className="min-w-0">
            {job.descriptionMd ? (
              <PostPanel job={job} />
            ) : (
              <p className="rounded-control bg-amber-50 px-3.5 py-2.5 text-[14px] text-amber-700">
                No public posting was found for this role, so WERA has no description. Open it on LinkedIn to read it, then paste the description to get a full score.
              </p>
            )}
          </div>
          <div className="space-y-5 text-small">
            {!fit.roughOnly ? (
              <div>
                <h4 className="mb-2 font-bold text-forest-900">Evidence</h4>
                <ul className="space-y-2">
                  {SUBSCORES.filter(([k]) => fit[k].jobQuote).map(([k, label]) => (
                    <li key={k}>
                      <span className="font-semibold">{label}:</span> “{fit[k].jobQuote}”
                      {fit[k].cvBulletIds.map((id) => (
                        <Chip key={id} variant="source" className="ml-1">
                          {id}
                        </Chip>
                      ))}
                    </li>
                  ))}
                  <li className="text-ink-2">
                    Freshness {fit.freshness}/10, from the posting date — {POSTED_SOURCE[job.postedPrecision]}. Scored by{" "}
                    <span className="font-mono">{fit.model}</span>.
                  </li>
                </ul>
                {fit.stale ? (
                  <p className="mt-2 rounded-control bg-amber-50 px-3.5 py-2.5 text-small text-amber-700">
                    This score was judged against an earlier version of your CV, a different scoring model, or a posting that has
                    since been rewritten. It&apos;s shown because it is the best evidence WERA has; it will be replaced when the
                    re-scoring pass reaches this job.
                  </p>
                ) : null}
              </div>
            ) : null}
            <div>
              <h4 className="mb-2 font-bold text-forest-900">Sources</h4>
              <ul className="space-y-1">
                {job.sources.map((s, i) => (
                  <li key={i}>
                    {sourceName(s.channel, s.detail)} · seen {formatAge(s.seenAt)}
                  </li>
                ))}
              </ul>
            </div>
            <div className="flex flex-col items-start gap-1">
              {job.applyUrl ? <ExternalA href={job.applyUrl}>Open the job post</ExternalA> : null}
              {job.linkedinUrl ? <ExternalA href={job.linkedinUrl}>Open on LinkedIn</ExternalA> : null}
              {job.linkedinUrl ? <p className="text-ink-2">Opens in your browser. WERA never fetches LinkedIn pages.</p> : null}
            </div>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

/**
 * The post laid out to read (18 Nov), or its summary. The summary is one fast-tier call on the first
 * click and kept on the job until the post changes, so it costs nothing to open again.
 */
function PostPanel({ job }: { job: ScoredJob }) {
  const [view, setView] = useState<"full" | "summary">("full");
  const [summary, setSummary] = useState(job.summary);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const showSummary = async () => {
    setView("summary");
    if (summary || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const next = await api.summariseJob(job.id);
      setSummary(next.summary);
      if (!next.summary) setError("No summary came back. Read the full post instead.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't summarise this post.");
    } finally {
      setBusy(false);
    }
  };

  const toggle = (pressed: boolean) =>
    cn(
      "inline-flex min-h-9 items-center border px-3 text-[13px] font-semibold transition-colors duration-150 pointer-coarse:min-h-11",
      pressed ? "border-forest-700 bg-accent text-white" : "border-line bg-surface text-ink hover:border-forest-700 hover:text-forest-700",
    );

  return (
    <div>
      <div role="group" aria-label="How to read the post" className="mb-3 flex flex-wrap gap-2">
        <button type="button" aria-pressed={view === "full"} onClick={() => setView("full")} className={toggle(view === "full")}>
          Full post
        </button>
        <button type="button" aria-pressed={view === "summary"} onClick={showSummary} className={toggle(view === "summary")}>
          Summary
        </button>
      </div>
      {view === "full" ? (
        job.descriptionView ? (
          <JobDescription view={job.descriptionView} />
        ) : (
          <Markdown source={job.descriptionMd ?? ""} />
        )
      ) : busy ? (
        <p className="text-[14px] text-ink-2" role="status">
          Reading the post…
        </p>
      ) : error ? (
        <p className="rounded-control bg-amber-50 px-3.5 py-2.5 text-[14px] text-amber-700">{error}</p>
      ) : summary ? (
        <JobSummaryView summary={summary} />
      ) : null}
    </div>
  );
}

function ExternalA({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="inline-flex min-h-8 items-center gap-1 font-semibold text-forest-700 underline underline-offset-2 hover:text-forest-900 pointer-coarse:min-h-11"
    >
      {children}
      <ExternalLink className="size-3.5" aria-hidden="true" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}
