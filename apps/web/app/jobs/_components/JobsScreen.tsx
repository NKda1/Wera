"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { api, ApiRequestError, type Decision } from "@/lib/api";
import { formatDate, formatLocation, formatPostedAge, isNew, URGENCY_GROUPS } from "@/lib/format";
import type { IntakeChannel, IntakeRun, ScoredJob, SearchProfile, ShortlistMeta } from "@/lib/types";
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

type Meta = ShortlistMeta;
/** "urgency" groups rather than re-sorting; see the `groups` memo below. */
type Sort = "score" | "date" | "urgency";

/** Which facet a chip belongs to. Chips in the same group are **or**-ed, different groups are
 *  and-ed — so London + Bristol means "either", which is the only reading anyone expects.
 *  All-and was the old behaviour and made any second location chip return nothing. */
type FacetGroup = "place" | "level" | "work" | "source" | "score";

type Facet = { id: string; group: FacetGroup; label: string; count: number; test: (j: ScoredJob) => boolean };

const GROUP_LABELS: Record<FacetGroup, string> = {
  place: "Where",
  level: "Level",
  work: "Working",
  source: "Found by",
  score: "Fit",
};

// How many places to offer. Enough to cover a real shortlist's spread without a chip row that
// wraps four times; the rest are reachable by sorting and reading.
const MAX_PLACES = 8;

const SOURCE_LABELS: Record<string, string> = {
  board_search: "Board search",
  email_alert: "Alerts",
  watchlist: "Watchlist",
  manual: "Pasted",
};

/** The filters this shortlist can actually offer, counted from the jobs on it.
 *
 * This was five hardcoded chips, one of which was a literal `London` — on a shortlist carrying
 * London, Bristol, Paris, Amsterdam, San Francisco and Dublin. A filter list that doesn't know
 * what it is filtering can only ever be right by luck.
 *
 * Places group on `location.city` rather than `location.raw` on purpose: the same city arrives
 * written four ways ("London", "London, UK", "London, Greater London, England, GBR"), and four
 * chips for one city is worse than none.
 */
function facetsFor(jobs: ScoredJob[]): Facet[] {
  const out: Facet[] = [];
  const tally = (key: (j: ScoredJob) => string | undefined) => {
    const counts = new Map<string, number>();
    for (const j of jobs) {
      const k = key(j);
      if (k) counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  };

  for (const [city, count] of tally((j) => j.location.city?.trim()).slice(0, MAX_PLACES)) {
    out.push({ id: `place:${city}`, group: "place", label: city, count, test: (j) => j.location.city?.trim() === city });
  }
  for (const [level, count] of tally((j) => j.seniority ?? undefined)) {
    out.push({
      id: `level:${level}`,
      group: "level",
      label: level.replace(/_/g, " "),
      count,
      test: (j) => j.seniority === level,
    });
  }
  for (const [mode, count] of tally((j) => j.remote ?? undefined)) {
    out.push({ id: `work:${mode}`, group: "work", label: mode, count, test: (j) => j.remote === mode });
  }
  for (const [channel, count] of tally((j) => j.sources[0]?.channel)) {
    out.push({
      id: `source:${channel}`,
      group: "source",
      label: SOURCE_LABELS[channel] ?? channel,
      count,
      // Any source, not just the first: a job found by two channels belongs under both.
      test: (j) => j.sources.some((s) => s.channel === channel),
    });
  }
  // Only offered when they'd divide the list. A "Fit 8+" chip that matches everything, or
  // nothing, is a button that does nothing.
  for (const floor of [8, 6]) {
    const count = jobs.filter((j) => j.fit.total >= floor).length;
    if (count && count < jobs.length) {
      out.push({ id: `score:${floor}`, group: "score", label: `${floor}+`, count, test: (j) => j.fit.total >= floor });
    }
  }
  return out;
}

const REJECT_REASONS = ["Wrong level", "Wrong location", "Not my field", "Pay too low", "Already applied elsewhere"];

const NEW_FROM: Record<string, string> = {
  board_search: "the search",
  email_alert: "alert emails",
  watchlist: "employer feeds",
  manual: "jobs you added",
};

/** SRCH-25: "12 new from the search on 11 Nov", or "12 new since 9 Nov" when they came from more
 *  than one place. Empty when nothing is new. */
function newSummary(jobs: ScoredJob[]): string {
  const found = jobs.flatMap((j) => (j.foundBy ? [j.foundBy] : []));
  if (!found.length) return "";
  const channels = new Set(found.map((f) => f.channel));
  const dates = found.map((f) => f.at).sort();
  if (channels.size === 1) {
    return `${found.length} new from ${NEW_FROM[found[0].channel] ?? "a search"} on ${formatDate(dates[dates.length - 1])}`;
  }
  return `${found.length} new since ${formatDate(dates[0])}`;
}

export function JobsScreen({
  jobs,
  meta,
  profile,
  sites,
  cvVersion,
  today,
}: {
  jobs: ScoredJob[];
  meta: Meta;
  profile: SearchProfile;
  sites: SiteOption[];
  cvVersion: number;
  today: string;
}) {
  const router = useRouter();
  const toast = useToast();
  // Facet ids, not a closed union: they are derived from the jobs, so the set depends on what
  // this shortlist happens to hold.
  const [filters, setFilters] = useState<string[]>([]);
  const [sort, setSort] = useState<Sort>("score");
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [busyId, setBusyId] = useState<string>();
  const [rejecting, setRejecting] = useState<ScoredJob>();
  const [duplicate, setDuplicate] = useState<{ job: ScoredJob; message: string }>();
  const [topN, setTopN] = useState(false);
  const [importing, setImporting] = useState(false);
  const [searching, setSearching] = useState(false);
  const [runIds, setRunIds] = useState<string[]>(meta.activeRunIds);
  const [showHidden, setShowHidden] = useState(false);
  const [showAwaiting, setShowAwaiting] = useState(false);
  const [scoringMore, setScoringMore] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  // SRCH-25: opened in this session. The tag goes at once; the order doesn't change until the next
  // load, because a card that jumped down the list the moment it was clicked would be worse.
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [newOnly, setNewOnly] = useState(false);

  const live = jobs.filter((j) => !removed.has(j.id));
  const fresh = (j: ScoredJob) => isNew(j) && !seen.has(j.id);
  const freshJobs = live.filter(fresh);
  // Rank is the fit position in the whole shortlist, so it stays stable under filters.
  const rankOf = useMemo(() => new Map([...live].sort((a, b) => b.fit.total - a.fit.total).map((j, i) => [j.id, i + 1])), [live]);
  const facets = useMemo(() => facetsFor(live), [live]);
  const visible = useMemo(() => {
    // Grouped first, so chips within a group are an "either" and groups are an "and". An id with
    // no matching facet — a place that has dropped off the shortlist since it was picked — simply
    // isn't here, so a stale selection quietly stops applying instead of hiding everything.
    const chosen = new Map<FacetGroup, Facet[]>();
    for (const f of facets.filter((f) => filters.includes(f.id))) {
      chosen.set(f.group, [...(chosen.get(f.group) ?? []), f]);
    }
    const list = live.filter(
      (j) => (!newOnly || (isNew(j) && !seen.has(j.id))) && [...chosen.values()].every((group) => group.some((f) => f.test(j))),
    );
    return sort === "date" ? [...list].sort((a, b) => (b.postedAt ?? "").localeCompare(a.postedAt ?? "")) : list;
  }, [live, facets, filters, sort, newOnly, seen]);
  // "By urgency" groups rather than re-sorts: inside a group the new jobs come first (SRCH-25) and
  // then the best match, because once you've decided what to look at today you want what you
  // haven't seen, and the best of it, at the top. New is read from the server's answer, not from
  // this session's opens, so opening a card never moves it.
  const groups = useMemo(() => {
    if (sort !== "urgency") return null;
    return URGENCY_GROUPS.map((group) => ({
      ...group,
      jobs: visible.filter((j) => j.urgency === group.id).sort((a, b) => Number(isNew(b)) - Number(isNew(a))),
    })).filter((g) => g.jobs.length);
  }, [visible, sort]);
  const top = [...visible].sort((a, b) => b.fit.total - a.fit.total).slice(0, 3);

  const markSeen = (job: ScoredJob) => {
    if (!fresh(job)) return;
    setSeen((s) => new Set(s).add(job.id));
    // Best effort: a lost request means the tag comes back on the next load, nothing worse.
    api.markSeen(job.id).catch(() => undefined);
  };

  const markAllSeen = async () => {
    try {
      await api.markAllSeen();
      setSeen((s) => new Set([...s, ...freshJobs.map((j) => j.id)]));
      setNewOnly(false);
      router.refresh();
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't mark them seen." });
    }
  };

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

  // SRCH-13: ask which postings are still there. Minutes of HTTP in the background, so this
  // reports that it started and the result shows up on the next load — a sweep nobody can see
  // running is still better than a page held open for it.
  const recheckJobs = async () => {
    setRechecking(true);
    try {
      await api.recheck();
      toast({ tone: "ok", message: "Checking which postings are still open. Closed ones leave the list as they're found." });
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't start the check." });
    } finally {
      setRechecking(false);
    }
  };

  // SRCH-26: a search stops at 8–12 and leaves the rest. This brings the next 8–12: jobs already
  // saved but not scored first, then the postings the search queued, then searches it hadn't sent.
  const findMore = async () => {
    setScoringMore(true);
    try {
      const { runIds: ids } = await api.scoreMore();
      setRunIds(ids);
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't find more jobs." });
    } finally {
      setScoringMore(false);
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
          {freshJobs.length ? (
            <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-small">
              <span className="font-semibold text-forest-900">{newSummary(freshJobs)}</span>
              <button type="button" onClick={markAllSeen} className="font-semibold text-forest-700 underline underline-offset-2 hover:text-forest-900">
                Mark all seen
              </button>
            </p>
          ) : null}
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
        {/* `onBatch` is what makes the first eight readable while the next eight are still being
            scored. Without it the shortlist only changed when the whole run finished, so a
            search produced nothing to look at for its first several minutes. */}
        {runIds.length ? <RunPanel runIds={runIds} onFinished={onRunsFinished} onBatch={() => router.refresh()} /> : null}
        <ProfileCard profile={profile} channels={meta.channels} sites={sites} cvVersion={cvVersion} onPasteJob={() => setImporting(true)} />
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
        {/* Built from the shortlist, grouped, with counts. Counts are the part that makes a facet
            usable: a chip that says "Bristol 12" is a decision you can make before clicking. */}
        <div className="flex flex-col gap-2">
          <div className="scrollbar-none -mx-4 flex gap-2 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:px-0" role="group" aria-label="Filter jobs">
            <FilterChip
              active={filters.length === 0 && !newOnly}
              onClick={() => {
                setFilters([]);
                setNewOnly(false);
              }}
            >
              All {live.length}
            </FilterChip>
            {freshJobs.length ? (
              <FilterChip active={newOnly} onClick={() => setNewOnly((n) => !n)}>
                New only {freshJobs.length}
              </FilterChip>
            ) : null}
          </div>
          {(Object.keys(GROUP_LABELS) as FacetGroup[]).map((group) => {
            const options = facets.filter((f) => f.group === group);
            if (options.length < 2) return null; // one option filters nothing out
            return (
              <div
                key={group}
                className="scrollbar-none -mx-4 flex items-center gap-2 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:px-0"
                role="group"
                aria-label={GROUP_LABELS[group]}
              >
                <span className="shrink-0 text-small text-ink-2">{GROUP_LABELS[group]}</span>
                {options.map((f) => (
                  <FilterChip
                    key={f.id}
                    active={filters.includes(f.id)}
                    onClick={() => setFilters((fs) => (fs.includes(f.id) ? fs.filter((x) => x !== f.id) : [...fs, f.id]))}
                  >
                    {f.label} <span className="font-normal opacity-70">{f.count}</span>
                  </FilterChip>
                ))}
              </div>
            );
          })}
        </div>
        <label className="flex items-center gap-2 text-small text-ink-2">
          Sort
          <Select value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="min-w-36 text-[14px]">
            <option value="score">Best match</option>
            <option value="date">Newest</option>
            <option value="urgency">By urgency</option>
          </Select>
        </label>
      </div>

      <section aria-label="Shortlisted jobs" className="mt-4 flex flex-col gap-4">
        {groups
          ? groups.map((group) => (
              <section key={group.id} aria-labelledby={`group-${group.id}`} className="flex flex-col gap-4">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line pb-2">
                  <h2 id={`group-${group.id}`} className="text-h2">
                    {group.label}
                  </h2>
                  <span className="text-small font-semibold text-forest-700">{group.jobs.length}</span>
                  <span className="text-small text-ink-2">{group.hint}</span>
                </div>
                {group.jobs.map((job) => (
                  <JobCard
                    key={job.id}
                    job={job}
                    rank={rankOf.get(job.id) ?? 0}
                    isNew={fresh(job)}
                    busy={busyId === job.id}
                    onOpen={() => markSeen(job)}
                    onApprove={() => decide(job, { action: "approve" })}
                    onSnooze={(days) => decide(job, { action: "snooze", days })}
                    onReject={() => setRejecting(job)}
                  />
                ))}
              </section>
            ))
          : visible.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                rank={rankOf.get(job.id) ?? 0}
                isNew={fresh(job)}
                busy={busyId === job.id}
                onOpen={() => markSeen(job)}
                onApprove={() => decide(job, { action: "approve" })}
                onSnooze={(days) => decide(job, { action: "snooze", days })}
                onReject={() => setRejecting(job)}
              />
            ))}
        {live.length > 0 ? (
          <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-2 py-2">
            <Button variant="secondary" onClick={findMore} busy={scoringMore} disabled={runIds.length > 0}>
              Find up to {meta.findMoreSize} more
            </Button>
            <span className="text-small text-ink-2">Waiting jobs first, then what your last search queued, then new searches.</span>
          </div>
        ) : null}
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
                <Button
                  variant="secondary"
                  onClick={() => {
                    setFilters([]);
                    setNewOnly(false);
                  }}
                >
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
            {meta.hiddenTotal} hidden by your profile
          </button>
          {" · "}
          {/* SRCH-13. Free — employers' own feeds, plain HTTP and a headless browser for the
              boards that render their closure in JavaScript — so there is nothing to confirm
              before running it. It also runs on startup when it is due, which is the fix for it
              having been scheduled for 07:00 on a process nobody leaves running. */}
          <button
            type="button"
            onClick={recheckJobs}
            disabled={rechecking}
            className="font-semibold text-forest-700 underline underline-offset-2 disabled:text-ink-2 disabled:no-underline"
          >
            {rechecking ? "Checking…" : "Check which are still open"}
          </button>
        </p>
        {/* MATCH-05 caps scoring per day, so a big first run leaves jobs queued. This used to be
            the count and nothing else, which made a job WERA had found and kept invisible until
            something scored it. They are listed now: a queued job is still a job you can read. */}
        {meta.awaitingScore > 0 ? (
          <p>
            <button
              type="button"
              onClick={() => setShowAwaiting((s) => !s)}
              aria-expanded={showAwaiting}
              className="font-semibold text-forest-700 underline underline-offset-2"
            >
              {meta.awaitingScore} more {meta.awaitingScore === 1 ? "job is" : "jobs are"} waiting to be scored
            </button>{" "}
            — best match first. &ldquo;Find more&rdquo; scores these before it searches.
          </p>
        ) : null}
        {showAwaiting ? (
          <ul className="space-y-2">
            {meta.awaiting.map((j) => (
              <li key={j.id} className="flex flex-wrap items-center justify-between gap-3 rounded-control border border-line bg-white px-4 py-2.5">
                {/* No score ring, for the same reason the hidden list has none: nothing has
                    judged this job yet, and a zero would read as a verdict rather than a queue
                    position. */}
                <span>
                  <span className="block font-semibold text-ink">
                    {j.title} · {j.company}
                  </span>
                  <span>
                    {formatLocation(j)} · {formatPostedAge(j)} · not scored yet
                  </span>
                </span>
                {j.applyUrl ? (
                  <a
                    href={j.applyUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-semibold text-forest-700 underline underline-offset-2"
                  >
                    Read the posting
                  </a>
                ) : null}
              </li>
            ))}
            {meta.awaitingScore > meta.awaiting.length ? (
              <li className="px-4 text-small text-ink-2">
                Showing {meta.awaiting.length} of {meta.awaitingScore}.
              </li>
            ) : null}
          </ul>
        ) : null}
        {showHidden ? (
          <ul className="space-y-2">
            {meta.hidden.map((j) => (
              <li key={j.id} className="flex flex-wrap items-center justify-between gap-3 rounded-control border border-line bg-white px-4 py-2.5">
                {/* No score ring: these were filtered before any model call (MATCH-01), so
                    there is no fit to show. The ring used to render `fit.total` next to the
                    words "Filtered before any model call", which was a score nothing produced. */}
                <span>
                  <span className="block font-semibold text-ink">
                    {j.title} · {j.company}
                  </span>
                  <span>{j.hiddenReason}. Filtered before any model call.</span>
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
