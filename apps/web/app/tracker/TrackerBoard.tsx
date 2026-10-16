"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ALLOWED, STATUS_LABEL } from "@/lib/applications";
import { formatAgo, formatDate, formatLocation } from "@/lib/format";
import type { ApplicationStatus, ApplicationWithJob } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { cn } from "@/lib/ui/cn";
import { Dialog } from "@/lib/ui/Dialog";
import { Input, Label, Select, Textarea } from "@/lib/ui/Field";
import { useToast } from "@/lib/ui/Toast";
import { DraftProgress } from "../review/_components/DraftProgress";

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
    const t = setInterval(() => router.refresh(), 1000);
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

  return (
    <div>
      <div className="mx-auto max-w-[920px] lg:mx-0 lg:max-w-none">
        <h1 className="text-[34px] leading-[1.05] font-bold tracking-[-0.025em] sm:text-display">Tracker</h1>
        <p className="mt-2 text-small text-ink-2">
          {count("submitted")} submitted · {count("interview")} interviewing · {count("offer")} offers
          {due ? <span className="font-semibold text-amber-700"> · {due} follow-up{due > 1 ? "s" : ""} due</span> : null}. You move every card; WERA never submits anything.
        </p>
      </div>

      <div className="scrollbar-none mt-6 -mx-4 flex snap-x scroll-px-4 gap-4 overflow-x-auto px-4 pb-4 sm:-mx-8 sm:scroll-px-8 sm:px-8 lg:-mx-12 lg:scroll-px-12 lg:px-12">
        {COLUMNS.map((col) => {
          const items = apps.filter((a) => col.statuses.includes(a.status));
          return (
            <section key={col.id} aria-labelledby={`col-${col.id}`} className="flex w-[280px] shrink-0 snap-start flex-col rounded-card bg-mint-100/60 p-3">
              <h2 id={`col-${col.id}`} className="mb-3 flex items-center justify-between px-1 text-[14px] font-bold">
                {col.title}
                <span className="rounded-full bg-white px-2 py-0.5 text-[12px] tabular-nums text-ink-2">{items.length}</span>
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
    <li className="rounded-control border border-line bg-white p-3.5 shadow-card">
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

      <div className="mt-2 text-small">
        {app.status === "drafting" && app.draft ? <DraftProgress step={app.draft.step} /> : null}
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
        {app.notes && !open ? <p className="mt-1.5 line-clamp-3 rounded-md bg-mint-50 px-2 py-1.5 text-ink">{app.notes}</p> : null}
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
