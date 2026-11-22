"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, useTransition } from "react";
import { api } from "@/lib/api";
import { formatTime, upskillAnchor } from "@/lib/format";
import type { SkillDemand, UpskillsReport } from "@/lib/types";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import {
  CATEGORIES,
  WINDOWS,
  filtersHref,
  focusCategory,
  focusStatus,
  jobsAsking,
  missingOnly,
  pickView,
  scope,
  toggleMissingOnly,
  toggleQuickWins,
  type Filters,
  type Status,
} from "@/lib/upskills";
import { EducationCard } from "./EducationCard";
import { GapTable } from "./GapTable";
import { CategoryBars, CoverageDonut, DemandBars, STATUS, StatusLegend } from "./SkillCharts";

const chip = (on: boolean) =>
  cn("inline-flex items-center rounded-none px-2.5 py-1 pointer-coarse:min-h-10", on ? "bg-accent text-white" : "bg-mint-100 text-forest-900 hover:bg-mint-200");
const winAnchor = (term: string) => `win-${upskillAnchor(term).slice("skill-".length)}`;

/** UPSK-04, amended 20 Nov: the page's filters and everything they scope, in one client component.
 *
 * The agent sends both views — scored jobs only, and with the near misses — each with its own
 * charts, so the near-miss switch, the category, the status filter and "Only what I'm missing"
 * change the page at once, with no round trip; each is written to the URL (`replaceState`), so a
 * view can be shared and survives a reload. Only the window asks the agent again, as a link. The
 * filters sit in one row above the charts and scope every chart, figure and list below them, so
 * the numbers always agree (the user, 20 Nov: the charts didn't move with the near misses). */
export function UpskillsBoard({ report, initial }: { report: UpskillsReport; initial: Filters }) {
  const fresh = useFollowsSearches(report.version);
  const [filters, setFilters] = useState<Filters>(initial);
  const [highlight, setHighlight] = useState<{ term: string; at: number } | null>(null);
  const [win, setWin] = useState<string | null>(null);
  const { days, category, near, show } = filters;
  const view = useMemo(() => pickView(report, near), [report, near]);
  const s = useMemo(() => scope(view, filters), [view, filters]);
  const nearly = report.withNearMisses?.nearMisses ?? 0;
  const jobs = view.jobs;
  const onlyMissing = missingOnly(show);

  // The URL follows the filters, without a navigation: the data is already here.
  useEffect(() => {
    const next = filtersHref(filters);
    if (`${window.location.pathname}${window.location.search}` !== next) window.history.replaceState(null, "", next);
  }, [filters]);

  const set = (patch: Partial<Filters>) => {
    setHighlight(null);
    setWin(null);
    setFilters((f) => ({ ...f, ...patch }));
  };
  const onStatus = (st: Status) => set({ show: focusStatus(show, st) });
  const onSkill = (d: SkillDemand) => {
    if (d.status === "missing") {
      setHighlight({ term: d.term, at: Date.now() });
      window.history.replaceState(null, "", `${filtersHref(filters)}#${upskillAnchor(d.term)}`);
    } else if (d.status === "quick_win") {
      setWin(d.term);
      requestAnimationFrame(() => {
        const el = document.getElementById(winAnchor(d.term));
        el?.scrollIntoView({ block: "center" });
        el?.focus({ preventScroll: true });
      });
    }
  };

  const shownLabel = onlyMissing
    ? show.includes("quick_win")
      ? "Showing only what your skills list lacks: skills not on your CV, and quick wins."
      : "Showing only the skills your CV doesn't show yet."
    : show.length === 1
      ? `Showing only skills ${STATUS[show[0]].short}.`
      : "Showing every skill these jobs ask for.";

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5">
      <header>
        <h1 className="text-h1">Upskills</h1>
        <p className="mt-1.5 max-w-3xl text-ink-2">
          What the {jobs} job{jobs === 1 ? "" : "s"} WERA scored for you in the last {days} days ask for, against what your CV shows — the skills
          worth learning first, and the ones you could already claim.
          {view.nearMisses
            ? ` Charts and gaps also count ${view.nearMisses} job${view.nearMisses === 1 ? "" : "s"} you nearly matched — titles close to yours that the filters left out — at half weight.`
            : ""}{" "}
          A job your CV matched poorly counts more: its gaps say most about what to improve.
        </p>
        <nav aria-label="Filter" className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2 text-small">
          <span className="flex flex-wrap gap-1.5" role="group" aria-label="Window">
            {WINDOWS.map((d) => (
              <Link key={d} href={filtersHref({ ...filters, days: d })} aria-current={d === days ? "page" : undefined} className={chip(d === days)}>
                {d} days
              </Link>
            ))}
          </span>
          {report.families.filter((f) => f.jobs + f.nearMisses > 0).length > 1 ? (
            // UPSK-07 (21 Nov): which kind of role the jobs are. A quant desk asks a finance CV for
            // C++; that is a direction, but a different one from what the person's own field asks.
            <span className="flex flex-wrap gap-1.5" role="group" aria-label="Kind of role">
              <Link href={filtersHref({ ...filters, family: null })} aria-current={!filters.family ? "page" : undefined} className={chip(!filters.family)}>
                All roles
              </Link>
              {report.families
                .filter((f) => f.jobs + f.nearMisses > 0 && f.family !== "other")
                .map((f) => (
                  <Link
                    key={f.family}
                    href={filtersHref({ ...filters, family: f.family })}
                    aria-current={filters.family === f.family ? "page" : undefined}
                    className={chip(filters.family === f.family)}
                    title={`${f.jobs} on your list${f.nearMisses ? `, ${f.nearMisses} nearly matched` : ""}`}
                  >
                    {f.label}
                    {f.family === report.ownFamily ? " (yours)" : ""}
                  </Link>
                ))}
            </span>
          ) : null}
          <span className="flex flex-wrap gap-1.5" role="group" aria-label="Kind of skill">
            {CATEGORIES.map((c) => (
              <button key={c.id} type="button" onClick={() => set({ category: c.id })} aria-pressed={c.id === category} className={chip(c.id === category)}>
                {c.label}
              </button>
            ))}
          </span>
          <span className="flex flex-wrap gap-1.5">
            {nearly ? (
              <button type="button" role="switch" aria-checked={near} onClick={() => set({ near: !near })} className={chip(near)}>
                Include near misses
              </button>
            ) : null}
            <button type="button" role="switch" aria-checked={onlyMissing} onClick={() => set({ show: toggleMissingOnly(show) })} className={chip(onlyMissing)}>
              Only what I&apos;m missing
            </button>
            {onlyMissing ? (
              <button
                type="button"
                role="switch"
                aria-checked={show.includes("quick_win")}
                onClick={() => set({ show: toggleQuickWins(show) })}
                className={chip(show.includes("quick_win"))}
              >
                With quick wins
              </button>
            ) : null}
          </span>
        </nav>
        <p className="mt-2 flex flex-wrap items-center gap-x-2 text-small text-ink-2">
          <span aria-live="polite">{fresh.refreshing ? "Updating with your latest jobs…" : fresh.at ? `Updated ${formatTime(fresh.at)} · follows each search` : "Follows each search"}</span>
          <button type="button" onClick={fresh.refresh} disabled={fresh.refreshing} className="font-semibold text-forest-700 underline-offset-2 hover:underline disabled:opacity-60">
            Refresh
          </button>
        </p>
        <p className="sr-only" aria-live="polite">
          {shownLabel} {s.demand.length} skills charted, {s.missing.length} gaps listed.
        </p>
      </header>

      {jobs === 0 ? (
        <Card className="p-5 text-ink-2">No scored jobs in this window yet. Run a search and this fills in from what those posts ask for.</Card>
      ) : (
        <>
          <section aria-label="At a glance" className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat
              label="Jobs analysed"
              value={view.nearMisses ? `${jobs} + ${view.nearMisses}` : String(jobs)}
              note={view.nearMisses ? `scored in ${days} days + nearly matched` : `scored in the last ${days} days`}
            />
            <Stat label="Most-asked skills you show" value={`${s.shownOnCv} of ${s.top.length}`} note="on your CV, listed or in a line" />
            <Stat label="Quick wins" value={String(s.allQuickWins)} note="on your CV but not your skills list" />
            <Stat
              label="Biggest gap"
              value={s.biggestGap?.term ?? "None"}
              note={s.biggestGap ? `asked by ${jobsAsking(s.biggestGap, jobs)}` : "your CV shows the top skills"}
            />
          </section>

          <div className="flex flex-col gap-1.5">
            <p className="text-small text-ink-2">{shownLabel}</p>
            <StatusLegend show={show} onStatus={onStatus} near={view.nearMisses > 0} />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <CoverageDonut coverage={s.coverage} jobs={jobs} nearMisses={view.nearMisses} weight={view.nearWeight} show={show} onStatus={onStatus} />
            <CategoryBars
              rows={s.byCategory}
              weight={view.nearWeight}
              nearMisses={view.nearMisses}
              category={category}
              show={show}
              onCategory={(c) => set({ category: focusCategory(category, c) })}
            />
          </div>

          {s.demand.length ? (
            <DemandBars demand={s.demand} jobs={jobs} nearMisses={view.nearMisses} highlight={highlight?.term ?? win} onSkill={onSkill} />
          ) : (
            <Card className="p-5 text-ink-2">No skill in this view. Click a status again, or switch off &ldquo;Only what I&apos;m missing&rdquo;, to see the rest.</Card>
          )}

          {(category === "all" || category === "qualification") && show.includes("missing") ? (
            <EducationCard education={view.education} qualifications={s.qualifications} jobs={jobs} />
          ) : null}

          {s.quickWins.length ? (
            <Card className="p-5" aria-labelledby="wins-title">
              <h2 id="wins-title" className="text-[16px] font-bold">
                Already on your CV — say so
              </h2>
              <p className="mt-1 text-small text-ink-2">
                Posts ask for these, and a line on your CV shows each one, but your skills list doesn&apos;t name them. Adding them is the quickest ATS
                gain there is — <Link href="/cv" className="underline hover:text-forest-700">add them on your CV</Link> if you&apos;d claim them.
              </p>
              <ul className="mt-3 grid gap-x-6 gap-y-2.5 md:grid-cols-2">
                {s.quickWins.map((w) => (
                  <li
                    key={w.term}
                    id={winAnchor(w.term)}
                    tabIndex={-1}
                    className={cn("scroll-mt-20 p-1 text-[14px] outline-none focus-visible:ring-2 focus-visible:ring-forest-700", win === w.term && "bg-mint-100")}
                  >
                    <span className="font-semibold">{w.term}</span>
                    <span className="text-ink-2"> · {jobsAsking(w, jobs)}</span>
                    {w.line ? <p className="line-clamp-2 text-small text-ink-2">“{w.line}”</p> : null}
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          <GapTable missing={s.missing} jobs={jobs} highlight={highlight} />
          {show.includes("missing") && !s.missing.length ? (
            <Card className="p-5 text-ink-2">Nothing missing in this category — your CV shows what these posts ask for.</Card>
          ) : null}
        </>
      )}
    </div>
  );
}

/** 22 Nov: the page follows each search. The report is worked out on the server when the page
 *  loads, so a search finished since — or the near misses read on their own thread after it, or a
 *  decision — never reached the charts without a reload (the user: Upskills wasn't refreshing with
 *  every search). Every 15 s while the tab is visible, and on coming back to it, the page asks the
 *  agent what the report is built from (`GET /upskills/version`, a few counts) and refreshes the
 *  server data when that has moved; the filters, held here, stay as they are. */
const CHECK_EVERY_MS = 15_000;

function useFollowsSearches(version: string) {
  const router = useRouter();
  const [refreshing, startTransition] = useTransition();
  // Set after the page loads, not while it renders, so the server's and the browser's clocks agree.
  const [at, setAt] = useState<string>();
  useEffect(() => setAt(new Date().toISOString()), [version]);
  useEffect(() => {
    if (!version) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const now = await api.upskillsVersion();
        if (!stopped && now.version !== version) startTransition(() => router.refresh());
      } catch {
        // The agent is down or busy: the next check tries again, and the page keeps what it shows.
      }
    };
    const loop = () => {
      timer = setTimeout(async () => {
        await check();
        if (!stopped) loop();
      }, CHECK_EVERY_MS);
    };
    const onBack = () => void check();
    loop();
    document.addEventListener("visibilitychange", onBack);
    window.addEventListener("focus", onBack);
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onBack);
      window.removeEventListener("focus", onBack);
    };
  }, [version, router]);
  return { at, refreshing, refresh: () => startTransition(() => router.refresh()) };
}

/** A headline number: label, value, and what it counts. */
function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <Card className="p-4">
      <p className="text-small text-ink-2">{label}</p>
      <p className="mt-1 text-[26px] leading-tight font-semibold text-ink [overflow-wrap:anywhere]">{value}</p>
      <p className="mt-0.5 text-small text-ink-2">{note}</p>
    </Card>
  );
}
