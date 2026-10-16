"use client";

import { useEffect, useRef, useState } from "react";
import { api, type Estimate } from "@/lib/api";
import type { IntakeChannel, IntakeRun, SearchProfile } from "@/lib/types";
import { Button, Spinner } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { Dialog } from "@/lib/ui/Dialog";
import { Checkbox } from "@/lib/ui/Field";
import { ProgressBar } from "@/lib/ui/ProgressSteps";

const NAMES: Record<IntakeChannel, string> = { board_search: "Board search", email_alert: "Alert inbox", watchlist: "ATS watchlist" };

/** PRF-05: show what a search will cost before it runs. */
export function SearchDialog({
  profile,
  onClose,
  onStart,
}: {
  profile: SearchProfile;
  onClose: () => void;
  onStart: (channels: IntakeChannel[]) => Promise<void>;
}) {
  const enabled = {
    board_search: profile.channels.boardSearch,
    email_alert: profile.channels.inbox,
    watchlist: profile.channels.watchlist,
  };
  const [chosen, setChosen] = useState<IntakeChannel[]>((Object.keys(enabled) as IntakeChannel[]).filter((c) => enabled[c]));
  const [est, setEst] = useState<Estimate>();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api.estimate(chosen).then((e) => live && setEst(e), () => undefined);
    return () => {
      live = false;
    };
  }, [chosen]);

  const toggle = (c: IntakeChannel, on: boolean) => setChosen((cs) => (on ? [...cs, c] : cs.filter((x) => x !== c)));

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
export function RunPanel({ runIds, onFinished }: { runIds: string[]; onFinished: (runs: IntakeRun[]) => void }) {
  const [runs, setRuns] = useState<IntakeRun[]>([]);
  const finished = useRef(false);
  const onDone = useRef(onFinished);
  onDone.current = onFinished;

  useEffect(() => {
    finished.current = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await Promise.all(runIds.map((id) => api.getRun(id)));
        setRuns(next);
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
      <ul className="mt-4 grid gap-4 md:grid-cols-3">
        {runs.map((r) => (
          <li key={r.id}>
            <div className="mb-1.5 flex items-baseline justify-between gap-2 text-small">
              <span className="font-semibold">{NAMES[r.channel]}</span>
              <span className="text-ink-2">{runStatus(r)}</span>
            </div>
            <ProgressBar value={r.progress} label={`${NAMES[r.channel]} progress`} />
          </li>
        ))}
      </ul>
    </Card>
  );
}

function runStatus(r: IntakeRun): string {
  if (r.status === "cancelled") return "Cancelled";
  if (r.status === "failed") return r.errors[0]?.message ?? "Failed";
  if (r.status === "running") {
    if (r.channel === "board_search") return `${r.queries} / 20 queries`;
    return r.channel === "email_alert" ? "Reading alert emails…" : "Checking feeds…";
  }
  const found = r.newJobs ? `${r.newJobs} new ${r.newJobs === 1 ? "job" : "jobs"}` : "nothing new";
  if (r.channel === "board_search") return `Done · ${found} · ${r.creditsUsed} credits`;
  return `Done · ${found}`;
}
