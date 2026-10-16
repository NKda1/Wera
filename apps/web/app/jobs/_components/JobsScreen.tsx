"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, ApiRequestError, type Decision } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { shortlistMeta } from "@/lib/server/store";
import type { IntakeChannel, IntakeRun, ScoredJob, SearchProfile } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import { Dialog } from "@/lib/ui/Dialog";
import { Label, Select, Textarea } from "@/lib/ui/Field";
import { Pill } from "@/lib/ui/Pill";
import { ScoreRing } from "@/lib/ui/ScoreRing";
import { useToast } from "@/lib/ui/Toast";
import { ImportDialog } from "./ImportDialog";
import { JobCard } from "./JobCard";
import { ProfileCard, type SiteOption } from "./ProfileCard";
import { RunPanel, SearchDialog } from "./SearchRun";

type Meta = ReturnType<typeof shortlistMeta>;
type Filter = "london" | "graduate" | "alerts" | "score8" | "remote";

const FILTERS: { id: Filter; label: string; test: (j: ScoredJob) => boolean }[] = [
  { id: "london", label: "London", test: (j) => j.location.city === "London" },
  { id: "graduate", label: "Graduate", test: (j) => j.seniority === "graduate" || /graduate|new grad/i.test(j.title) },
  { id: "alerts", label: "From alerts", test: (j) => j.sources.some((s) => s.channel === "email_alert") },
  { id: "score8", label: "Score 8+", test: (j) => j.fit.total >= 8 },
  { id: "remote", label: "Remote or hybrid", test: (j) => j.remote === "remote" || j.remote === "hybrid" },
];

const REJECT_REASONS = ["Wrong level", "Wrong location", "Not my field", "Pay too low", "Already applied elsewhere"];

export function JobsScreen({
  jobs,
  meta,
  profile,
  sites,
  today,
}: {
  jobs: ScoredJob[];
  meta: Meta;
  profile: SearchProfile;
  sites: SiteOption[];
  today: string;
}) {
  const router = useRouter();
  const toast = useToast();
  const [filters, setFilters] = useState<Filter[]>([]);
  const [sort, setSort] = useState<"score" | "date">("score");
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [busyId, setBusyId] = useState<string>();
  const [rejecting, setRejecting] = useState<ScoredJob>();
  const [duplicate, setDuplicate] = useState<{ job: ScoredJob; message: string }>();
  const [topN, setTopN] = useState(false);
  const [importing, setImporting] = useState(false);
  const [searching, setSearching] = useState(false);
  const [runIds, setRunIds] = useState<string[]>(meta.activeRunIds);
  const [showHidden, setShowHidden] = useState(false);

  const live = jobs.filter((j) => !removed.has(j.id));
  // Rank is the fit position in the whole shortlist, so it stays stable under filters.
  const rankOf = useMemo(() => new Map([...live].sort((a, b) => b.fit.total - a.fit.total).map((j, i) => [j.id, i + 1])), [live]);
  const visible = useMemo(() => {
    const list = live.filter((j) => filters.every((f) => FILTERS.find((x) => x.id === f)!.test(j)));
    return sort === "date" ? [...list].sort((a, b) => (b.postedAt ?? "").localeCompare(a.postedAt ?? "")) : list;
  }, [live, filters, sort]);
  const top = [...visible].sort((a, b) => b.fit.total - a.fit.total).slice(0, 3);

  const hide = (id: string) => setRemoved((s) => new Set(s).add(id));
  const unhideLocal = (id: string) =>
    setRemoved((s) => {
      const n = new Set(s);
      n.delete(id);
      return n;
    });

  const decide = async (job: ScoredJob, d: Decision): Promise<boolean> => {
    setBusyId(job.id);
    hide(job.id);
    try {
      await api.decide(job.id, d);
      if (d.action === "approve") {
        toast({ tone: "ok", message: `Approved ${job.title}. Drafting your pack…`, action: { label: "View", onClick: () => router.push("/review") } });
      } else if (d.action === "snooze") {
        const until = new Date(Date.now() + (d.days ?? 1) * 864e5).toISOString();
        toast({ tone: "ok", message: `Snoozed ${job.company} until ${formatDate(until, { weekday: "short", day: "numeric", month: "short" })}.` });
      } else {
        toast({ tone: "ok", message: `Rejected ${job.title} at ${job.company}.` });
      }
      router.refresh();
      return true;
    } catch (err) {
      unhideLocal(job.id);
      if (err instanceof ApiRequestError && err.code === "DUPLICATE_APPLICATION") {
        setDuplicate({ job, message: err.message });
      } else {
        toast({ tone: "error", message: err instanceof Error ? err.message : "That didn't work." });
      }
      return false;
    } finally {
      setBusyId(undefined);
    }
  };

  const approveTop = async () => {
    setTopN(false);
    let ok = 0;
    for (const job of top) {
      setBusyId(job.id);
      hide(job.id);
      try {
        await api.decide(job.id, { action: "approve" });
        ok++;
      } catch (err) {
        unhideLocal(job.id);
        toast({ tone: "attention", message: `${job.company}: ${err instanceof Error ? err.message : "not approved"}` });
      }
    }
    setBusyId(undefined);
    if (ok) toast({ tone: "ok", message: `Approved ${ok} ${ok === 1 ? "job" : "jobs"}. Drafting packs…`, action: { label: "View", onClick: () => router.push("/review") } });
    router.refresh();
  };

  const startSearch = async (channels: IntakeChannel[]) => {
    try {
      const { runIds: ids } = await api.startRun(channels);
      setRunIds(ids);
      setSearching(false);
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't start the search." });
    }
  };

  const onRunsFinished = (runs: IntakeRun[]) => {
    const added = runs.reduce((n, r) => n + (r.status === "succeeded" ? r.newJobs : 0), 0);
    const cancelled = runs.some((r) => r.status === "cancelled");
    if (runs.length) {
      toast({
        tone: "ok",
        message: cancelled ? "Search cancelled. Anything already found was kept." : added ? `Search finished: ${added} new ${added === 1 ? "job" : "jobs"} added.` : "Search finished: nothing new since the last run.",
      });
    }
    router.refresh();
    setTimeout(() => setRunIds([]), 2500);
  };

  const counts = meta.found;

  return (
    <div className="mx-auto max-w-[1080px]">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4 lg:flex-nowrap">
        <div className="min-w-0 flex-1">
          <Pill tone="green">Gate 1 · waiting for you</Pill>
          <h1 className="mt-3 text-[34px] leading-[1.05] font-bold tracking-[-0.025em] sm:text-display">Today&apos;s shortlist</h1>
          <p className="mt-2 text-small text-ink-2">
            {today} · {counts.total} found from your CV · {counts.board} board search · {counts.inbox} alert emails · {counts.watchlist} watchlist ·{" "}
            {live.length} strong {live.length === 1 ? "match" : "matches"}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="secondary" onClick={() => setSearching(true)} disabled={runIds.length > 0}>
            Search now
          </Button>
          <Button onClick={() => setTopN(true)} disabled={top.length === 0}>
            Approve top {top.length || 3}
          </Button>
        </div>
      </header>

      <div className="mt-6 flex flex-col gap-4">
        {runIds.length ? <RunPanel runIds={runIds} onFinished={onRunsFinished} /> : null}
        <ProfileCard profile={profile} channels={meta.channels} sites={sites} onPasteJob={() => setImporting(true)} />
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        <div className="scrollbar-none -mx-4 flex gap-2 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:px-0" role="group" aria-label="Filter jobs">
          <FilterChip active={filters.length === 0} onClick={() => setFilters([])}>
            All {live.length}
          </FilterChip>
          {FILTERS.map((f) => (
            <FilterChip key={f.id} active={filters.includes(f.id)} onClick={() => setFilters((fs) => (fs.includes(f.id) ? fs.filter((x) => x !== f.id) : [...fs, f.id]))}>
              {f.label}
            </FilterChip>
          ))}
        </div>
        <label className="flex items-center gap-2 text-small text-ink-2">
          Sort
          <Select value={sort} onChange={(e) => setSort(e.target.value as "score" | "date")} className="min-w-36 text-[14px]">
            <option value="score">Best match</option>
            <option value="date">Newest</option>
          </Select>
        </label>
      </div>

      <section aria-label="Shortlisted jobs" className="mt-4 flex flex-col gap-4">
        {visible.map((job) => (
          <JobCard
            key={job.id}
            job={job}
            rank={rankOf.get(job.id) ?? 0}
            isNew={meta.newJobIds.includes(job.id)}
            busy={busyId === job.id}
            onApprove={() => decide(job, { action: "approve" })}
            onSnooze={(days) => decide(job, { action: "snooze", days })}
            onReject={() => setRejecting(job)}
          />
        ))}
        {visible.length === 0 ? (
          <Card className="px-6 py-10 text-center">
            <p className="text-h2">{live.length ? "No jobs match these filters" : "You're all caught up"}</p>
            <p className="mx-auto mt-2 max-w-md text-ink-2">
              {live.length
                ? "Clear a filter to see the rest of today's shortlist."
                : "Every job on today's shortlist has a decision. Run a search, or check the packs waiting for you in Review."}
            </p>
            <div className="mt-5 flex justify-center gap-2">
              {live.length ? (
                <Button variant="secondary" onClick={() => setFilters([])}>
                  Clear filters
                </Button>
              ) : (
                <>
                  <Button variant="secondary" onClick={() => setSearching(true)}>
                    Search now
                  </Button>
                  <Button onClick={() => router.push("/review")}>Go to Review</Button>
                </>
              )}
            </div>
          </Card>
        ) : null}
      </section>

      <footer className="mt-6 space-y-3 text-small text-ink-2">
        <p>
          Scores come from the job description and your master CV. The total is weighted in code; you can change the weights in{" "}
          <Link href="/settings#weights" className="font-semibold text-forest-700 underline underline-offset-2">
            Settings
          </Link>
          .
        </p>
        <p>
          {meta.snoozed} snoozed · {meta.rejected} rejected ·{" "}
          <button type="button" onClick={() => setShowHidden((s) => !s)} aria-expanded={showHidden} className="font-semibold text-forest-700 underline underline-offset-2">
            {meta.hidden.length} hidden by your profile
          </button>
        </p>
        {showHidden ? (
          <ul className="space-y-2">
            {meta.hidden.map((j) => (
              <li key={j.id} className="flex flex-wrap items-center justify-between gap-3 rounded-control border border-line bg-white px-4 py-2.5">
                <span className="flex items-center gap-3">
                  <ScoreRing score={j.fit.total} size="sm" />
                  <span>
                    <span className="block font-semibold text-ink">
                      {j.title} · {j.company}
                    </span>
                    <span>{j.hiddenReason}. Filtered before any model call.</span>
                  </span>
                </span>
                <Button
                  variant="secondary"
                  onClick={async () => {
                    try {
                      await api.decide(j.id, { action: "unhide" });
                      toast({ tone: "ok", message: `${j.title} is back on your shortlist.` });
                      router.refresh();
                    } catch (err) {
                      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't unhide." });
                    }
                  }}
                >
                  Unhide
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </footer>

      {importing ? <ImportDialog onClose={() => setImporting(false)} /> : null}
      {searching ? <SearchDialog profile={profile} onClose={() => setSearching(false)} onStart={startSearch} /> : null}
      {rejecting ? <RejectDialog job={rejecting} onClose={() => setRejecting(undefined)} onReject={(reason) => (setRejecting(undefined), decide(rejecting, { action: "reject", reason }))} /> : null}

      <Dialog
        open={topN}
        onClose={() => setTopN(false)}
        title={`Approve the top ${top.length}?`}
        description="WERA drafts a tailored CV, cover letter and ATS report for each. You review every pack before anything is used."
        footer={
          <>
            <Button variant="secondary" onClick={() => setTopN(false)}>
              Cancel
            </Button>
            <Button onClick={approveTop}>Approve {top.length}</Button>
          </>
        }
      >
        <ol className="space-y-2">
          {top.map((j) => (
            <li key={j.id} className="flex items-center gap-3">
              <ScoreRing score={j.fit.total} roughOnly={j.fit.roughOnly} size="sm" />
              <span>
                <span className="block font-semibold">{j.title}</span>
                <span className="text-small text-ink-2">{j.company}</span>
              </span>
            </li>
          ))}
        </ol>
        {filters.length ? <p className="mt-4 text-small text-ink-2">Only jobs that match your current filters are included.</p> : null}
      </Dialog>

      <Dialog
        open={!!duplicate}
        onClose={() => setDuplicate(undefined)}
        size="sm"
        title="You've applied for this before"
        description={duplicate?.message}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDuplicate(undefined)}>
              Don&apos;t approve
            </Button>
            <Button
              onClick={() => {
                const job = duplicate!.job;
                setDuplicate(undefined);
                decide(job, { action: "approve", force: true });
              }}
            >
              Approve anyway
            </Button>
          </>
        }
      >
        <p className="text-[15px]">
          A second application to the same company for the same title within 90 days is usually noticed. Check the{" "}
          <Link href="/tracker" className="font-semibold text-forest-700 underline underline-offset-2">
            tracker
          </Link>{" "}
          first.
        </p>
      </Dialog>
    </div>
  );
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "min-h-11 shrink-0 rounded-full border px-4 text-[14px] font-semibold transition-colors",
        active ? "border-forest-700 bg-forest-700 text-white" : "border-line bg-white text-ink hover:border-mint-200 hover:bg-mint-50",
      )}
    >
      {children}
    </button>
  );
}

function RejectDialog({ job, onClose, onReject }: { job: ScoredJob; onClose: () => void; onReject: (reason?: string) => void }) {
  const [reason, setReason] = useState<string>();
  const [other, setOther] = useState("");
  return (
    <Dialog
      open
      onClose={onClose}
      size="sm"
      title={`Reject ${job.title}?`}
      description="A reason is optional. WERA uses your decisions to tune the scoring weights later."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="dark" onClick={() => onReject(other.trim() || reason)}>
            Reject
          </Button>
        </>
      }
    >
      <fieldset>
        <legend className="mb-2 text-[14px] font-semibold">Reason</legend>
        <div className="flex flex-wrap gap-2">
          {REJECT_REASONS.map((r) => (
            <FilterChip key={r} active={reason === r} onClick={() => setReason(reason === r ? undefined : r)}>
              {r}
            </FilterChip>
          ))}
        </div>
      </fieldset>
      <div className="mt-4">
        <Label htmlFor="reject-other">Something else</Label>
        <Textarea id="reject-other" rows={2} value={other} onChange={(e) => setOther(e.target.value)} className="min-h-0" />
      </div>
    </Dialog>
  );
}
