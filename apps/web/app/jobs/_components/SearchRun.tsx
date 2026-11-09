"use client";

import { useEffect, useRef, useState } from "react";
import { api, type Estimate } from "@/lib/api";
import type { IntakeChannel, IntakeRun, RunPhase, SearchChannel, SearchProfile } from "@/lib/types";
import { Button, Spinner } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { Dialog } from "@/lib/ui/Dialog";
import { Checkbox } from "@/lib/ui/Field";
import { ProgressBar, ProgressSteps, type StepState } from "@/lib/ui/ProgressSteps";

// Keyed on the wide type, because `RunPanel` has to name a re-score run as well as a channel.
const NAMES: Record<IntakeChannel, string> = {
  board_search: "Board search",
  email_alert: "Alert inbox",
  watchlist: "ATS watchlist",
  rescore: "Re-scoring your shortlist",
  score_more: "Scoring more jobs",
};

/** UX-01: what each stage is called, in words someone can act on.
 *
 * Every one of these was already a step in the intake graph; none of them had a name the person
 * could see, because `phase` had three values and "searching" stood for all of the fetching. */
const PHASE_LABELS: Record<RunPhase, string> = {
  queued: "Starting",
  discovering: "Finding boards",
  searching: "Searching",
  scraping: "Reading pages",
  feeds: "Checking feeds",
  reading: "Reading alerts",
  collapsing: "Merging duplicates",
  filtering: "Filtering",
  scoring: "Scoring",
  closing: "Checking still open",
  done: "Done",
};

/** The unit `done`/`total` are counted in, per stage. They change meaning with the phase —
 *  queries while searching, pages while scraping, jobs while scoring — and labelling them all
 *  "queries" is what left the old panel reading "22 / 22 queries" through the scrape loop. */
const PHASE_UNITS: Partial<Record<RunPhase, string>> = {
  searching: "queries",
  scraping: "pages",
  feeds: "employers",
  scoring: "jobs",
};

/** Which stages each kind of run actually passes through, in order. `discovering` is added by
 *  `stepsFor` only while it is happening, because it is weekly and most runs skip it. */
const STEPS: Record<IntakeChannel, RunPhase[]> = {
  board_search: ["searching", "scraping", "collapsing", "filtering", "scoring", "closing"],
  email_alert: ["reading", "collapsing", "filtering", "scoring", "closing"],
  watchlist: ["feeds", "collapsing", "filtering", "scoring", "closing"],
  rescore: ["scoring"],
  score_more: ["scoring"],
};

/** PRF-05: show what a search will cost before it runs. */
export function SearchDialog({
  profile,
  onClose,
  onStart,
}: {
  profile: SearchProfile;
  onClose: () => void;
  onStart: (channels: SearchChannel[]) => Promise<void>;
}) {
  const enabled: Record<SearchChannel, boolean> = {
    board_search: profile.channels.boardSearch,
    email_alert: profile.channels.inbox,
    watchlist: profile.channels.watchlist,
  };
  const [chosen, setChosen] = useState<SearchChannel[]>((Object.keys(enabled) as SearchChannel[]).filter((c) => enabled[c]));
  const [est, setEst] = useState<Estimate>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api.estimate(chosen).then((e) => live && setEst(e), () => undefined);
    return () => {
      live = false;
    };
  }, [chosen]);

  const toggle = (c: SearchChannel, on: boolean) => setChosen((cs) => (on ? [...cs, c] : cs.filter((x) => x !== c)));

  return (
    <Dialog
      open
      onClose={onClose}
      title="Search now"
      description="Runs your search profile across the channels you pick, then scores new jobs against your master CV."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={!chosen.length}
            busy={busy}
            onClick={async () => {
              setBusy(true);
              await onStart(chosen);
              setBusy(false);
            }}
          >
            Run search
          </Button>
        </>
      }
    >
      <fieldset>
        <legend className="sr-only">Channels</legend>
        <Checkbox
          label="Board search"
          disabled={!enabled.board_search}
          checked={chosen.includes("board_search")}
          onChange={(e) => toggle("board_search", e.target.checked)}
          description={
            enabled.board_search
              ? `${est?.queries ?? "…"} queries (${profile.titles.length} titles × ${profile.locations.length} locations) · up to ${est?.credits ?? "…"} Firecrawl credits`
              : "Off in your search profile"
          }
        />
        <Checkbox
          label="Alert inbox"
          disabled={!enabled.email_alert}
          checked={chosen.includes("email_alert")}
          onChange={(e) => toggle("email_alert", e.target.checked)}
          description={enabled.email_alert ? "Read-only check of new alert emails · no credits" : "Off in your search profile"}
        />
        <Checkbox
          label="ATS watchlist"
          disabled={!enabled.watchlist}
          checked={chosen.includes("watchlist")}
          onChange={(e) => toggle("watchlist", e.target.checked)}
          description={enabled.watchlist ? `${est?.companies || 32} companies · free public APIs` : "Off in your search profile"}
        />
      </fieldset>
      <p className="mt-4 rounded-control bg-mint-50 px-3.5 py-2.5 text-small text-ink-2">
        {est?.demo
          ? "Demo mode: results come from recorded searches and nothing is spent."
          : `Scoring new jobs costs about $${(est?.usd ?? 0).toFixed(2)} in model calls.`}
      </p>
    </Dialog>
  );
}

/** UX-01: live progress for each channel, with Cancel. */
export function RunPanel({
  runIds,
  onFinished,
  onBatch,
}: {
  runIds: string[];
  onFinished: (runs: IntakeRun[]) => void;
  /** MATCH-05: a batch of scores has landed, so the shortlist is worth re-reading. */
  onBatch?: () => void;
}) {
  const [runs, setRuns] = useState<IntakeRun[]>([]);
  const finished = useRef(false);
  const onDone = useRef(onFinished);
  onDone.current = onFinished;
  const onBatchRef = useRef(onBatch);
  onBatchRef.current = onBatch;
  // How many whole batches have been shown. Scoring reports after every job, and refreshing the
  // page every eleven seconds would be worse than not refreshing at all — so the list is re-read
  // when a batch boundary is crossed, which is the point at which there is something new to see.
  const batches = useRef(0);

  useEffect(() => {
    finished.current = false;
    batches.current = 0;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await Promise.all(runIds.map((id) => api.getRun(id)));
        setRuns(next);
        const done = batchesDone(next);
        if (done > batches.current) {
          batches.current = done;
          onBatchRef.current?.();
        }
        if (next.every((r) => r.status !== "running" && r.status !== "queued")) {
          if (!finished.current) {
            finished.current = true;
            onDone.current(next);
          }
          return;
        }
      } catch {
        // A restarted demo server forgets runs; stop quietly.
        if (!finished.current) {
          finished.current = true;
          onDone.current([]);
        }
        return;
      }
      timer = setTimeout(poll, 600);
    };
    poll();
    return () => clearTimeout(timer);
  }, [runIds]);

  const running = runs.some((r) => r.status === "running");

  return (
    <Card className="animate-fade-in p-5 sm:p-6" aria-live="polite" aria-labelledby="run-title">
      <div className="flex items-center justify-between gap-4">
        <h2 id="run-title" className="flex items-center gap-2 text-[18px] font-bold">
          {running ? <Spinner className="text-forest-700" /> : null}
          {running ? "Running your search…" : "Search finished"}
        </h2>
        {running ? (
          <Button variant="secondary" onClick={() => Promise.all(runs.filter((r) => r.status === "running").map((r) => api.cancelRun(r.id)))}>
            Cancel
          </Button>
        ) : null}
      </div>
      {/* One row per channel rather than three columns: a stage checklist needs the width, and
          a percentage with no named stage beside it was the original complaint. */}
      <ul className="mt-4 flex flex-col gap-5">
        {runs.map((r) => (
          <li key={r.id}>
            <div className="mb-1.5 flex items-baseline justify-between gap-2 text-small">
              <span className="font-semibold">{NAMES[r.channel]}</span>
              <span className="text-ink-2">{runStatus(r)}</span>
            </div>
            <ProgressBar value={r.progress} label={`${NAMES[r.channel]} progress`} />
            {r.status === "running" ? (
              <>
                <ProgressSteps className="mt-2.5" steps={stepsFor(r)} />
                {/* The noun. `phase` is an enum and has to stand for twenty queries or forty
                    jobs, so without this the active step stops changing for minutes at a time —
                    which reads as a hung process however much the bar moves. */}
                {r.detail ? <p className="mt-1.5 text-small text-ink-2">{r.detail}</p> : null}
              </>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** How many whole batches of scoring have finished across these runs.
 *
 * Scoring is shared, so the runs report the same `done` — the maximum is the run's progress, not
 * the sum. Zero until the first batch completes, which is what stops the shortlist being
 * re-read before there is anything new on it. */
function batchesDone(runs: IntakeRun[]): number {
  return runs.reduce((most, r) => {
    const size = r.batchSize || 0;
    return size > 0 ? Math.max(most, Math.floor(r.done / size)) : most;
  }, 0);
}

/** The stages this run passes through, in order, with where it has got to.
 *
 * Taken from the channel rather than from `PHASE_ORDER` wholesale, because which stages apply
 * depends on what the run is: a board search scrapes pages, a watchlist reads feeds, a re-score
 * does neither. Showing a stage that will never happen is as misleading as showing none.
 */
function stepsFor(r: IntakeRun): { label: string; state: StepState; detail?: string }[] {
  const phases = STEPS[r.channel];
  // `discovering` is weekly (ATS-05) and most runs skip it, so it appears only while it is
  // actually happening — never as a pending step that may never arrive.
  const ordered = r.phase === "discovering" ? (["discovering", ...phases] as RunPhase[]) : phases;
  const at = ordered.indexOf(r.phase);
  return ordered.map((phase, i) => ({
    label: PHASE_LABELS[phase],
    // A phase WERA has moved past is done; the one it is in is active. An unknown phase (`at`
    // of -1) leaves everything "todo", which is the honest reading of "we don't know yet".
    state: at < 0 ? "todo" : i < at ? "done" : i === at ? "active" : "todo",
    detail: i === at ? countFor(r) : undefined,
  }));
}

/** How far through the current stage, in that stage's own unit. */
function countFor(r: IntakeRun): string | undefined {
  if (!r.total) return undefined;
  const unit = PHASE_UNITS[r.phase];
  return unit ? `${r.done} / ${r.total} ${unit}` : undefined;
}

function runStatus(r: IntakeRun): string {
  if (r.status === "cancelled") return "Cancelled";
  if (r.status === "failed") return r.errors[0]?.message ?? "Failed";
  if (r.status === "running") {
    // The stage name and its count, from the same two tables the checklist uses — so the line
    // above the bar and the active step below it can never disagree. The counts come from the
    // run rather than a constant: `queries` used to be a hardcoded 20, which was both frozen at
    // "0 / 20" for the whole search and wrong as soon as the query grid changed shape.
    const count = countFor(r);
    return count ? `${PHASE_LABELS[r.phase]} · ${count}` : `${PHASE_LABELS[r.phase]}…`;
  }
  // Neither of these *finds* anything, by design: they judge jobs WERA already has. "nothing
  // new" — true of every one of them ever — would read as a run that failed.
  if (r.channel === "rescore") return r.total ? `Done · ${r.total} re-scored` : "Done";
  if (r.channel === "score_more") return r.total ? `Done · ${r.total} scored` : "Done";
  const found = r.newJobs ? `${r.newJobs} new ${r.newJobs === 1 ? "job" : "jobs"}` : "nothing new";
  if (r.channel === "board_search") {
    // SRCH-10: what the crawl cache answered for free. Shown rather than netted off, because
    // "0 credits" on its own reads as a run that never happened — the "saved" clause is what
    // says the searches were answered, just not paid for again.
    const saved = r.creditsSaved ? ` · ${r.creditsSaved} saved` : "";
    // SRCH-05: how the pages were read. "18 pages free" is the number that says the local ladder
    // is working; a rising `fallbackPages` is a board that has changed shape, and seeing it here
    // is cheaper than seeing it on the invoice.
    const pages = pagesRead(r);
    return `Done · ${found} · ${r.creditsUsed} credits${saved}${pages}`;
  }
  return `Done · ${found}`;
}

function pagesRead(r: IntakeRun): string {
  if (!r.localPages && !r.fallbackPages) return "";
  if (!r.fallbackPages) return ` · ${r.localPages} ${r.localPages === 1 ? "page" : "pages"} read free`;
  return ` · ${r.localPages} free, ${r.fallbackPages} paid`;
}
