"use client";

import { ChevronDown } from "lucide-react";
import { useRouter } from "next/navigation";
import { useId, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { formatDate, formatLocation, formatPostedAge, previewLabel, previewsLeft, previewSource, sortPreviews, type PreviewLabel } from "@/lib/format";
import type { Job, PreviewCutoffs } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import { Menu } from "@/lib/ui/Menu";
import { ReasonDialog } from "@/lib/ui/ReasonDialog";
import { useToast } from "@/lib/ui/Toast";

// How many preview cards render before "Show more": a search can keep a hundred or more.
const PAGE = 20;

// Strong reads as the shortlist's own green; Good as a paler one; Possible stays neutral — it is
// a lead worth a look, not a warning, so it isn't amber.
const LABEL_STYLE: Record<PreviewLabel["id"], string> = {
  strong: "bg-mint-100 text-forest-900",
  good: "bg-mint-50 text-forest-900",
  possible: "border border-line text-ink-2",
};

/**
 * 21 Nov: "More found, not read yet" — leads the job APIs (and, 22 Nov, the hunter) found that a search had no lookups left
 * to read, ranked by how close their title and summary are to the CV (no AI has read them), and
 * collapsed by default under the scored jobs. "Read the full ad" finds the employer's own posting
 * and scores it, and the job joins the shortlist; Snooze and Reject decide on it as it is.
 */
export function PreviewsSection({ previews, total, cutoffs }: { previews: Job[]; total: number; cutoffs?: PreviewCutoffs | null }) {
  const router = useRouter();
  const toast = useToast();
  const headingId = useId();
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [shown, setShown] = useState(PAGE);
  const [gone, setGone] = useState<Set<string>>(new Set());
  const [reading, setReading] = useState<string>();
  const [busyId, setBusyId] = useState<string>();
  const [rejecting, setRejecting] = useState<Job>();

  const live = useMemo(() => sortPreviews(previews.filter((j) => !gone.has(j.id))), [previews, gone]);
  const left = previewsLeft(total, previews, gone);
  if (left <= 0) return null;

  const drop = (id: string) => setGone((s) => new Set(s).add(id));
  const keep = (id: string) =>
    setGone((s) => {
      const n = new Set(s);
      n.delete(id);
      return n;
    });

  const read = async (job: Job) => {
    setReading(job.id);
    try {
      const got = await api.readPreview(job.id);
      drop(job.id);
      if (got.job) {
        const fit = got.job.fit.total.toFixed(1);
        toast({
          tone: "ok",
          message: got.original
            ? `Read the full ad for ${job.title}: fit ${fit}. It's on your shortlist.`
            : `No employer ad was found for ${job.title}, so it's scored on the summary: fit ${fit} (rough). It's on your shortlist.`,
        });
      } else {
        toast({ tone: "attention", message: `Read in full, your profile hides ${job.title}: ${got.hiddenReason}.` });
      }
      router.refresh();
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't read the full ad." });
    } finally {
      setReading(undefined);
    }
  };

  const decide = async (job: Job, d: Parameters<typeof api.decide>[1]) => {
    setBusyId(job.id);
    drop(job.id);
    try {
      await api.decide(job.id, d);
      if (d.action === "snooze") {
        const until = new Date(Date.now() + (d.days ?? 1) * 864e5).toISOString();
        toast({ tone: "ok", message: `Snoozed ${job.title} until ${formatDate(until, { weekday: "short", day: "numeric", month: "short" })}.` });
      } else {
        toast({ tone: "ok", message: `Rejected ${job.title} (${previewSource(job)}).` });
      }
      router.refresh();
    } catch (err) {
      keep(job.id);
      toast({ tone: "error", message: err instanceof Error ? err.message : "That didn't work." });
    } finally {
      setBusyId(undefined);
    }
  };

  return (
    <section aria-labelledby={headingId} className="mt-10">
      <h2 id={headingId} className="border-b border-line pb-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls={listId}
          className="group -mx-1 inline-flex min-h-11 items-center gap-2 rounded-none px-1 text-left text-h2 hover:text-forest-700"
        >
          More found, not read yet ({left})
          <ChevronDown className={cn("size-4 shrink-0 text-ink-2 transition-transform group-hover:text-forest-700", open && "rotate-180")} aria-hidden="true" />
        </button>
      </h2>
      <p className="mt-2 text-small text-ink-2">
        Jobs WERA found that a search didn&apos;t read, best match to your CV first. Only a short summary or the link&apos;s own words are
        known and no AI has read them. Reading one reads the employer&apos;s own ad and scores it, which counts towards today&apos;s scoring
        limit.
      </p>

      {open ? (
        <ul id={listId} className="mt-4 flex flex-col gap-3">
          {live.slice(0, shown).map((job) => (
            <li key={job.id}>
              <PreviewCard
                job={job}
                label={previewLabel(job.previewMatch, cutoffs ?? undefined)}
                reading={reading === job.id}
                busy={busyId === job.id || (reading !== undefined && reading !== job.id)}
                onRead={() => read(job)}
                onSnooze={(days) => decide(job, { action: "snooze", days })}
                onReject={() => setRejecting(job)}
              />
            </li>
          ))}
          {shown < live.length ? (
            <li className="flex justify-center">
              <Button variant="secondary" onClick={() => setShown((n) => n + PAGE)}>
                Show {Math.min(PAGE, live.length - shown)} more
              </Button>
            </li>
          ) : null}
          {left > live.length ? (
            <li className="text-center text-small text-ink-2">
              Showing the best {live.length} of {left}.
            </li>
          ) : null}
        </ul>
      ) : null}

      {rejecting ? (
        <ReasonDialog
          title={`Reject ${rejecting.title}?`}
          action="Reject"
          onClose={() => setRejecting(undefined)}
          onConfirm={(reasonCode, reason) => (setRejecting(undefined), decide(rejecting, { action: "reject", reasonCode, reason }))}
        />
      ) : null}
    </section>
  );
}

function PreviewCard({
  job,
  label,
  reading,
  busy,
  onRead,
  onSnooze,
  onReject,
}: {
  job: Job;
  label: PreviewLabel;
  reading: boolean;
  busy: boolean;
  onRead: () => void;
  onSnooze: (days: 1 | 3 | 7) => void;
  onReject: () => void;
}) {
  const titleId = useId();
  const meta = [previewSource(job), formatLocation(job), formatPostedAge(job)].filter(Boolean).join(" · ");
  return (
    // An article, not a region: these aren't the shortlist, and a screen reader's landmarks list
    // shouldn't carry a hundred of them.
    <Card className="p-4 @2xl:p-5" role="article" aria-labelledby={titleId} aria-busy={reading || undefined}>
      <div className="flex flex-col gap-3 @2xl:flex-row @2xl:items-start @2xl:justify-between @2xl:gap-6">
        <div className="min-w-0">
          <p className="mb-1.5 flex flex-wrap items-center gap-2">
            <span className={cn("inline-flex rounded-none px-2.5 py-0.5 text-[12px] font-semibold", LABEL_STYLE[label.id])}>{label.label}</span>
            {/* SRCH-32: Adzuna's terms ask for its name, linked, on every job it found, at least
                116×23 px. The link is Adzuna's home page; the job's own tracked link is never
                requested by WERA. */}
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
          <h3 id={titleId} className="text-[17px] font-bold tracking-[-0.01em]">
            {job.title}
          </h3>
          <p className="mt-0.5 text-small text-ink-2">{meta}</p>
          {job.descriptionMd ? <p className="mt-2 line-clamp-3 text-[14px] leading-relaxed text-ink">{job.descriptionMd}</p> : null}
          {reading ? (
            <p role="status" className="mt-2 rounded-none bg-mint-50 px-3 py-2 text-small text-forest-900">
              {job.foundOn?.kind === "posting" ? "Reading the ad" : "Finding the employer\u2019s own ad, reading it"} and scoring it against your CV…
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-2 @2xl:flex-col">
          <Button onClick={onRead} busy={reading} disabled={reading || busy} className="flex-1 @max-xl:px-3 @2xl:flex-none">
            {reading ? "Reading…" : "Read the full ad"}
          </Button>
          <div className="flex flex-1 gap-2 @2xl:flex-none">
            <Menu
              className="flex-1"
              label={`Snooze ${job.title}`}
              items={[
                { label: "1 day", onSelect: () => onSnooze(1) },
                { label: "3 days", onSelect: () => onSnooze(3) },
                { label: "7 days", onSelect: () => onSnooze(7) },
              ]}
              trigger={({ open, toggle, id }) => (
                <Button variant="secondary" onClick={toggle} disabled={reading || busy} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} className="w-full @max-xl:px-3">
                  Snooze
                </Button>
              )}
            />
            <Button variant="ghost" onClick={onReject} disabled={reading || busy} className="flex-1 @max-xl:px-3">
              Reject
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}
