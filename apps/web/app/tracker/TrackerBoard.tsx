"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { ALLOWED, STATUS_LABEL } from "@/lib/applications";
import { formatAgo, formatDate, formatLocation } from "@/lib/format";
import type { ApplicationStatus, ApplicationWithJob } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { cn } from "@/lib/ui/cn";
import { Dialog } from "@/lib/ui/Dialog";
import { Input, Label, Select, Textarea } from "@/lib/ui/Field";
import { revealInline, useScrollEdges } from "@/lib/ui/scrollEdges";
import { useToast } from "@/lib/ui/Toast";
import { DraftProgress } from "../review/_components/DraftProgress";
import { JobLinks } from "@/lib/ui/JobLinks";

const COLUMNS: { id: string; title: string; statuses: ApplicationStatus[] }[] = [
  { id: "drafting", title: "Drafting", statuses: ["drafting", "changes_requested"] },
  { id: "review", title: "To review", statuses: ["ready_for_review"] },
  { id: "approved", title: "Approved", statuses: ["approved"] },
  { id: "submitted", title: "Submitted", statuses: ["submitted"] },
  { id: "interview", title: "Interview", statuses: ["interview"] },
  { id: "offer", title: "Offer", statuses: ["offer"] },
  { id: "closed", title: "Closed", statuses: ["rejected", "withdrawn"] },
];

const CONFIRM: Partial<Record<ApplicationStatus, string>> = {
  discarded: "The pack is deleted and the job leaves your pipeline.",
  withdrawn: "Marks the application as withdrawn. You'll still see it under Closed.",
  rejected: "Moves it to Closed. Add a note about what you heard, if anything.",
};

export function TrackerBoard({ apps }: { apps: ApplicationWithJob[] }) {
  const router = useRouter();
  const toast = useToast();
  const [confirm, setConfirm] = useState<{ app: ApplicationWithJob; to: ApplicationStatus }>();
  const drafting = apps.some((a) => a.status === "drafting");

  useEffect(() => {
    if (!drafting) return;
    const t = setInterval(() => router.refresh(), 2000);
    return () => clearInterval(t);
  }, [drafting, router]);

  const now = Date.now();
  const due = apps.filter((a) => a.status === "submitted" && a.followUpAt && new Date(a.followUpAt).getTime() <= now).length;
  const count = (s: ApplicationStatus) => apps.filter((a) => a.status === s).length;

  const move = async (app: ApplicationWithJob, to: ApplicationStatus) => {
    try {
      await api.patchApplication(app.id, { status: to });
      toast({ tone: "ok", message: to === "submitted" ? `${app.job.company}: marked submitted. Follow-up in 7 days.` : `${app.job.company} moved to ${STATUS_LABEL[to].toLowerCase()}.` });
      router.refresh();
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't move it." });
    }
  };

  const request = (app: ApplicationWithJob, to: ApplicationStatus) => (CONFIRM[to] ? setConfirm({ app, to }) : move(app, to));

  // Seven 280px columns are wider than any screen, and the board's scrollbar was hidden: nothing
  // said Interview, Offer or Closed existed past the edge. The strip above names every column with
  // its count, marks the ones in view, and scrolls the board to the one pressed.
  const board = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState<Set<string>>(new Set());
  useEffect(() => {
    const el = board.current;
    if (!el) return;
    const seen = new IntersectionObserver(
      (entries) =>
        setInView((prev) => {
          const next = new Set(prev);
          for (const e of entries) {
            const id = (e.target as HTMLElement).dataset.column!;
            if (e.isIntersecting) next.add(id);
            else next.delete(id);
          }
          return next;
        }),
      { root: el, threshold: 0.6 },
    );
    for (const col of el.querySelectorAll<HTMLElement>("[data-column]")) seen.observe(col);
    return () => seen.disconnect();
  }, []);
  // On a phone the strip is one row that scrolls sideways (wrapped, it took three rows of the
  // screen), and it follows the board: swipe to Offer and Offer's button comes into view.
  const strip = useRef<HTMLElement>(null);
  const stripFade = useScrollEdges(strip);
  useEffect(() => {
    const first = COLUMNS.find((c) => inView.has(c.id));
    if (first) revealInline(strip.current, strip.current?.querySelector<HTMLElement>(`[data-jump="${first.id}"]`) ?? null);
  }, [inView]);
  const jumpTo = (id: string) => {
    const el = board.current;
    const col = el?.querySelector<HTMLElement>(`[data-column="${id}"]`);
    if (!el || !col) return;
    const pad = parseFloat(getComputedStyle(el).scrollPaddingLeft) || 0;
    el.scrollTo({ left: col.getBoundingClientRect().left - el.getBoundingClientRect().left + el.scrollLeft - pad, behavior: "smooth" });
  };

  return (
    <div>
      <div className="mx-auto max-w-[920px] lg:mx-0 lg:max-w-none">
        <h1 className="text-[34px] leading-[1.05] font-bold tracking-[-0.025em] sm:text-display">Tracker</h1>
        <p className="mt-2 text-small text-ink-2">
          {count("submitted")} submitted · {count("interview")} interviewing · {count("offer")} offers
          {due ? <span className="font-semibold text-amber-700"> · {due} follow-up{due > 1 ? "s" : ""} due</span> : null}. You move every card; WERA never submits anything.
        </p>
      </div>

      <nav
        ref={strip}
        style={stripFade}
        aria-label="Jump to a column"
        className="scrollbar-none mt-6 -mx-4 flex gap-1.5 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0"
      >
        {COLUMNS.map((col) => {
          const n = apps.filter((a) => col.statuses.includes(a.status)).length;
          return (
            <button
              key={col.id}
              type="button"
              data-jump={col.id}
              onClick={() => jumpTo(col.id)}
              className={cn(
                "inline-flex min-h-9 shrink-0 items-center gap-1.5 border px-3 text-[13px] font-semibold transition-colors duration-150 pointer-coarse:min-h-11",
                inView.has(col.id) ? "border-forest-700 bg-mint-100 text-forest-900" : "border-line bg-surface text-ink-2 hover:border-forest-700 hover:text-forest-700",
              )}
            >
              {col.title}
              <span className="font-normal tabular-nums opacity-70">{n}</span>
            </button>
          );
        })}
      </nav>

      {/* Bleeds to the screen edge by exactly main's padding (`px-4 sm:px-10 lg:px-16`); it used to
          stop 8px short from 640px and 16px short from 1024px. The scrollbar shows where a mouse
          is the way to scroll it, and stays hidden on touch, where swiping is. */}
      <div
        ref={board}
        className="mt-4 -mx-4 flex snap-x scroll-px-4 gap-4 overflow-x-auto px-4 pb-4 [scrollbar-width:thin] pointer-coarse:scrollbar-none sm:-mx-10 sm:scroll-px-10 sm:px-10 lg:-mx-16 lg:scroll-px-16 lg:px-16"
      >
        {COLUMNS.map((col) => {
          const items = apps.filter((a) => col.statuses.includes(a.status));
          return (
            <section
              key={col.id}
              data-column={col.id}
              aria-labelledby={`col-${col.id}`}
              className="flex w-[280px] shrink-0 snap-start flex-col rounded-card bg-mint-100/60 p-3"
            >
              <h2 id={`col-${col.id}`} className="mb-3 flex items-center justify-between px-1 text-[14px] font-bold">
                {col.title}
                <span className="rounded-none bg-surface px-2 py-0.5 text-[12px] tabular-nums text-ink-2">{items.length}</span>
              </h2>
              <ul className="flex flex-col gap-2.5">
                {items.map((a) => (
                  <TrackerCard key={a.id} app={a} onMove={(to) => request(a, to)} />
                ))}
                {!items.length ? <li className="rounded-control border border-dashed border-line px-3 py-6 text-center text-small text-ink-2">Nothing here</li> : null}
              </ul>
            </section>
          );
        })}
      </div>

      <Dialog
        open={!!confirm}
        onClose={() => setConfirm(undefined)}
        size="sm"
        title={confirm ? `Move ${confirm.app.job.company} to ${STATUS_LABEL[confirm.to].toLowerCase()}?` : ""}
        description={confirm ? CONFIRM[confirm.to] : undefined}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(undefined)}>
              Cancel
            </Button>
            <Button
              variant={confirm?.to === "discarded" ? "danger" : "primary"}
              onClick={() => {
                const c = confirm!;
                setConfirm(undefined);
                move(c.app, c.to);
              }}
            >
              Move
            </Button>
          </>
        }
      />
    </div>
  );
}

function TrackerCard({ app, onMove }: { app: ApplicationWithJob; onMove: (to: ApplicationStatus) => void }) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState(app.notes ?? "");
  const [followUp, setFollowUp] = useState(app.followUpAt?.slice(0, 10) ?? "");
  const [saving, setSaving] = useState(false);
  const options = ALLOWED[app.status];
  const overdue = app.status === "submitted" && app.followUpAt && new Date(app.followUpAt).getTime() <= Date.now();
  const hasPack = app.status !== "drafting" && app.status !== "changes_requested";

  const saveDetails = async () => {
    setSaving(true);
    try {
      await api.patchApplication(app.id, { notes, followUpAt: followUp ? new Date(`${followUp}T09:00:00`).toISOString() : null });
      toast({ tone: "ok", message: "Saved." });
      setOpen(false);
      router.refresh();
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't save." });
    } finally {
      setSaving(false);
    }
  };

  return (
    <li className="rounded-control border border-line bg-surface p-3.5 shadow-card [overflow-wrap:anywhere]">
      <h3 className="text-[15px] leading-snug font-bold">
        {hasPack ? (
          <Link href={`/review/${app.id}`} className="hover:text-forest-700">
            {app.job.title}
          </Link>
        ) : (
          app.job.title
        )}
      </h3>
      <p className="text-small text-ink-2">
        {app.job.company} · {formatLocation(app.job)} · fit {app.job.fitTotal.toFixed(1)}
      </p>
      <JobLinks job={app.job} className="mt-1" />

      <div className="mt-2 text-small">
        {app.status === "drafting" && app.draft ? <DraftProgress step={app.draft.step} progress={app.draft.progress} /> : null}
        {app.status === "ready_for_review" ? (
          <Link href={`/review/${app.id}`} className="font-semibold text-forest-700 underline underline-offset-2">
            Waiting for your review
          </Link>
        ) : null}
        {app.status === "approved" ? <p className="text-ink-2">Approved {formatAgo(app.approvedAt)}. Apply on the employer&apos;s site, then mark it submitted.</p> : null}
        {app.submittedAt && app.status !== "approved" ? <p className="text-ink-2">Submitted {formatDate(app.submittedAt, { day: "numeric", month: "short" })}</p> : null}
        {app.followUpAt && (app.status === "submitted" || app.status === "interview") ? (
          <p className={cn(overdue ? "font-semibold text-amber-700" : "text-ink-2")}>
            {overdue ? "Follow-up due " : "Follow up "}
            {formatDate(app.followUpAt, { weekday: "short", day: "numeric", month: "short" })}
          </p>
        ) : null}
        {app.notes && !open ? <p className="mt-1.5 line-clamp-3 rounded-none bg-mint-50 px-2 py-1.5 text-ink">{app.notes}</p> : null}
      </div>

      {open ? (
        <div className="mt-3 space-y-3 border-t border-line pt-3">
          <div>
            <Label htmlFor={`${app.id}-notes`} className="text-small">
              Notes
            </Label>
            <Textarea id={`${app.id}-notes`} rows={3} className="min-h-0 text-[14px]" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          {app.status === "submitted" || app.status === "interview" ? (
            <div>
              <Label htmlFor={`${app.id}-fu`} className="text-small">
                Follow-up date
              </Label>
              <Input id={`${app.id}-fu`} type="date" value={followUp} onChange={(e) => setFollowUp(e.target.value)} />
            </div>
          ) : null}
          <div className="flex gap-2">
            <Button onClick={saveDetails} busy={saving} className="flex-1">
              Save
            </Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      <div className="mt-3 flex items-center gap-2">
        {options.length ? (
          <Select
            aria-label={`Move ${app.job.title} at ${app.job.company}`}
            value=""
            onChange={(e) => e.target.value && onMove(e.target.value as ApplicationStatus)}
            className="min-h-10 text-[14px]"
          >
            <option value="">Move to…</option>
            {options.map((s) => (
              <option key={s} value={s}>
                {s === "discarded" && app.status === "drafting" ? "Cancel draft" : STATUS_LABEL[s]}
              </option>
            ))}
          </Select>
        ) : (
          <span className="flex-1 text-small text-ink-2">{STATUS_LABEL[app.status]}</span>
        )}
        {!open ? (
          <Button variant="ghost" className="min-h-10 px-3 text-[14px]" onClick={() => setOpen(true)}>
            Notes
          </Button>
        ) : null}
      </div>
    </li>
  );
}
