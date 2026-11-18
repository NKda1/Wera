import type { Metadata } from "next";
import Link from "next/link";
import { agent } from "@/lib/server/agent";
import type { Coverage } from "@/lib/types";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import { GapTable } from "./_components/GapTable";
import { CategoryBars, CoverageDonut, DemandBars, StatusLegend } from "./_components/SkillCharts";

export const metadata: Metadata = { title: "Upskills" };

const WINDOWS = [30, 60, 90];
const CATEGORIES: { id: string; label: string }[] = [
  { id: "all", label: "All" },
  { id: "hard_skill", label: "Skills" },
  { id: "tool", label: "Tools" },
  { id: "methodology", label: "Methods" },
];
// The most-asked skills charted. Past fifteen the bars are one or two jobs each, which is the
// table's job, not a chart's.
const CHARTED = 15;
const NONE: Coverage = { covered: 0, quickWins: 0, missing: 0 };

// UPSK-01…04: what the jobs WERA scored for this person ask for against what their CV shows,
// worked out in the agent from the fit reports it already holds — no model call behind this page.
// Charts first (17 Nov): a long list of cards made the reader do the adding up; the page now does
// it and leads with the answer, and the detail is one table below.
export default async function UpskillsPage({ searchParams }: { searchParams: Promise<{ days?: string; category?: string }> }) {
  const params = await searchParams;
  const days = WINDOWS.includes(Number(params.days)) ? Number(params.days) : 60;
  const category = CATEGORIES.some((c) => c.id === params.category) ? params.category! : "all";
  const report = await agent.upskills(days);
  const inCategory = (x: { category?: string | null }) => category === "all" || x.category === category;
  // The filter scopes everything below it, so every number on the page is about the same slice.
  const missing = report.missing.filter(inCategory);
  const wins = report.quickWins.filter(inCategory);
  const demand = report.demand.filter(inCategory).slice(0, CHARTED);
  const coverage = category === "all" ? report.coverage : (report.byCategory.find((c) => c.category === category) ?? NONE);
  const href = (d: number, c: string) => `/upskills?days=${d}${c === "all" ? "" : `&category=${c}`}`;
  const shown = demand.filter((d) => d.status !== "missing").length;
  const gap = demand.find((d) => d.status === "missing");
  const jobs = report.jobsConsidered;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5">
      <header>
        <h1 className="text-h1">Upskills</h1>
        <p className="mt-1.5 max-w-3xl text-ink-2">
          What the {jobs} job{jobs === 1 ? "" : "s"} WERA scored for you in the last {days} days ask for, against what your CV shows — the skills
          worth learning first, and the ones you could already claim.
        </p>
        <nav aria-label="Filter" className="mt-3 flex flex-wrap gap-x-5 gap-y-2 text-small">
          <span className="flex flex-wrap gap-1.5">
            {WINDOWS.map((d) => (
              <Link
                key={d}
                href={href(d, category)}
                aria-current={d === days ? "page" : undefined}
                className={cn("inline-flex items-center rounded-none px-2.5 py-1 pointer-coarse:min-h-10", d === days ? "bg-accent text-white" : "bg-mint-100 text-forest-900")}
              >
                {d} days
              </Link>
            ))}
          </span>
          <span className="flex flex-wrap gap-1.5">
            {CATEGORIES.map((c) => (
              <Link
                key={c.id}
                href={href(days, c.id)}
                aria-current={c.id === category ? "page" : undefined}
                className={cn("inline-flex items-center rounded-none px-2.5 py-1 pointer-coarse:min-h-10", c.id === category ? "bg-accent text-white" : "bg-mint-100 text-forest-900")}
              >
                {c.label}
              </Link>
            ))}
          </span>
        </nav>
      </header>

      {jobs === 0 ? (
        <Card className="p-5 text-ink-2">No scored jobs in this window yet. Run a search and this fills in from what those posts ask for.</Card>
      ) : (
        <>
          <section aria-label="At a glance" className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Jobs analysed" value={String(jobs)} note={`scored in the last ${days} days`} />
            <Stat label="Most-asked skills you show" value={`${shown} of ${demand.length}`} note="on your CV, listed or in a line" />
            <Stat label="Quick wins" value={String(wins.length)} note="on your CV but not your skills list" />
            <Stat label="Biggest gap" value={gap?.term ?? "None"} note={gap ? `${gap.jobs} of ${jobs} jobs ask for it` : "your CV shows the top skills"} />
          </section>

          <StatusLegend />

          <div className={cn("grid gap-5", category === "all" && "lg:grid-cols-2")}>
            <CoverageDonut coverage={coverage} jobs={jobs} />
            {category === "all" ? <CategoryBars rows={report.byCategory} /> : null}
          </div>

          {demand.length ? <DemandBars demand={demand} jobs={jobs} /> : null}

          {wins.length ? (
            <Card className="p-5" aria-labelledby="wins-title">
              <h2 id="wins-title" className="text-[16px] font-bold">
                Already on your CV — say so
              </h2>
              <p className="mt-1 text-small text-ink-2">
                Posts ask for these, and a line on your CV shows each one, but your skills list doesn&apos;t name them. Adding them is the quickest ATS
                gain there is — <Link href="/cv" className="underline hover:text-forest-700">add them on your CV</Link> if you&apos;d claim them.
              </p>
              <ul className="mt-3 grid gap-x-6 gap-y-2.5 md:grid-cols-2">
                {wins.map((w) => (
                  <li key={w.term} className="text-[14px]">
                    <span className="font-semibold">{w.term}</span>
                    <span className="text-ink-2"> · {w.jobs} job{w.jobs === 1 ? "" : "s"}</span>
                    {w.line ? <p className="line-clamp-2 text-small text-ink-2">“{w.line}”</p> : null}
                  </li>
                ))}
              </ul>
            </Card>
          ) : null}

          <GapTable missing={missing} jobs={jobs} />
          {!missing.length ? <Card className="p-5 text-ink-2">Nothing missing in this category — your CV shows what these posts ask for.</Card> : null}
        </>
      )}
    </div>
  );
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
