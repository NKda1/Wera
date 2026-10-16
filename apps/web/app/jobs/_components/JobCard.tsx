"use client";

import { ChevronDown, ExternalLink } from "lucide-react";
import { useId, useState } from "react";
import { formatAge, formatLocation, formatSalary, sourceLine, sourceName } from "@/lib/format";
import type { ScoredJob } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { Chip } from "@/lib/ui/Chip";
import { cn } from "@/lib/ui/cn";
import { Markdown } from "@/lib/ui/Markdown";
import { Menu } from "@/lib/ui/Menu";
import { Pill } from "@/lib/ui/Pill";
import { ScoreRing } from "@/lib/ui/ScoreRing";
import { SubScoreBar } from "@/lib/ui/SubScoreBar";

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
  onApprove,
  onSnooze,
  onReject,
}: {
  job: ScoredJob;
  rank: number;
  isNew: boolean;
  busy: boolean;
  onApprove: () => void;
  onSnooze: (days: 1 | 3 | 7) => void;
  onReject: () => void;
}) {
  const [open, setOpen] = useState(false);
  const detailsId = useId();
  const { fit } = job;
  const meta = [job.company, formatLocation(job), formatSalary(job.salary), formatAge(job.postedAt)].join(" · ");

  return (
    <Card className={cn("animate-fade-in p-5 sm:p-6", isNew && "ring-2 ring-mint-200")} aria-labelledby={`${detailsId}-title`}>
      <div className="grid grid-cols-[64px_1fr] gap-x-4 gap-y-4 sm:gap-x-5 md:grid-cols-[64px_1fr_112px]">
        <div className="flex flex-col items-center gap-1.5">
          <ScoreRing score={fit.total} roughOnly={fit.roughOnly} />
          <span className="text-small text-ink-2">fit · #{rank}</span>
        </div>

        <div className="min-w-0">
          <h3 id={`${detailsId}-title`} className="text-h2">
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              aria-controls={detailsId}
              className="group -mx-1 inline-flex items-start gap-1.5 rounded-md px-1 text-left hover:text-forest-700"
            >
              {job.title}
              <ChevronDown className={cn("mt-1.5 size-4 shrink-0 text-ink-2 transition-transform group-hover:text-forest-700", open && "rotate-180")} aria-hidden="true" />
              <span className="sr-only">{open ? "Hide details" : "Show details"}</span>
            </button>
            {isNew ? (
              <Pill tone="green" dot={false} className="ml-2 align-middle text-[11px]">
                New
              </Pill>
            ) : null}
          </h3>
          <p className="mt-0.5 text-small text-ink-2">{meta}</p>
          <p className="mt-1.5">
            <span className="inline-flex rounded-[8px] bg-mint-100 px-2.5 py-0.5 text-[12px] font-semibold text-forest-900 sm:rounded-full">{sourceLine(job)}</span>
          </p>
        </div>

        <div className="col-span-2 grid gap-x-8 gap-y-1.5 sm:grid-cols-2 md:col-span-1 md:col-start-2">
          {SUBSCORES.map(([key, label]) => (
            <SubScoreBar key={key} label={label} score={fit[key].score} />
          ))}
        </div>

        <div className="col-span-2 rounded-control bg-mint-50 px-3.5 py-2.5 text-[14px] leading-relaxed md:col-span-1 md:col-start-2">
          {fit.roughOnly ? (
            <p>Only the title, company and location are known. Open it on LinkedIn to read the description; the score is a rough title match.</p>
          ) : (
            <p>
              <span>“{fit.jobQuote}”</span> — {fit.reason}
              {fit.cvBulletIds.length ? (
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
        </div>

        <div className="col-span-2 flex gap-2 md:col-span-1 md:col-start-3 md:row-span-3 md:row-start-1 md:flex-col">
          <Button onClick={onApprove} disabled={busy} className="flex-1 md:flex-none">
            Approve
          </Button>
          <Menu
            className="flex-1 md:flex-none"
            label={`Snooze ${job.title}`}
            items={[
              { label: "1 day", onSelect: () => onSnooze(1) },
              { label: "3 days", onSelect: () => onSnooze(3) },
              { label: "7 days", onSelect: () => onSnooze(7) },
            ]}
            trigger={({ open: menuOpen, toggle, id }) => (
              <Button variant="secondary" onClick={toggle} disabled={busy} aria-haspopup="menu" aria-expanded={menuOpen} aria-controls={menuOpen ? id : undefined} className="w-full">
                Snooze
              </Button>
            )}
          />
          <Button variant="ghost" onClick={onReject} disabled={busy} className="flex-1 md:flex-none">
            Reject
          </Button>
        </div>
      </div>

      {open ? (
        <div id={detailsId} className="mt-5 grid gap-6 border-t border-line pt-5 md:grid-cols-[1fr_260px] md:pl-[84px]">
          <div className="min-w-0">
            {job.descriptionMd ? (
              <Markdown source={job.descriptionMd} />
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
                    Freshness {fit.freshness}/10, from the posting date. Scored by <span className="font-mono">{fit.model}</span>.
                  </li>
                </ul>
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

function ExternalA({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className="inline-flex min-h-8 items-center gap-1 font-semibold text-forest-700 underline underline-offset-2 hover:text-forest-900">
      {children}
      <ExternalLink className="size-3.5" aria-hidden="true" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}
