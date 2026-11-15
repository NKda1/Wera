import type { Metadata } from "next";
import Link from "next/link";
import { agent } from "@/lib/server/agent";
import { upskillAnchor as anchor } from "@/lib/format";
import type { Upskill } from "@/lib/types";
import { Card } from "@/lib/ui/Card";
import { Chip, ChipList } from "@/lib/ui/Chip";
import { cn } from "@/lib/ui/cn";

export const metadata: Metadata = { title: "Upskills" };

const WINDOWS = [30, 60, 90];
const CATEGORIES: { id: string; label: string }[] = [
  { id: "all", label: "All" },
  { id: "hard_skill", label: "Skills" },
  { id: "tool", label: "Tools" },
  { id: "methodology", label: "Methods" },
];
const TREND: Record<Upskill["trend"], string> = { new: "New this fortnight", rising: "Asked more lately", steady: "Steady", falling: "Asked less lately" };

// UPSK-01…04: what the jobs WERA scored for this person ask for that their CV doesn't show, worked
// out in the agent from the fit reports it already holds — no model call behind this page.
export default async function UpskillsPage({ searchParams }: { searchParams: Promise<{ days?: string; category?: string }> }) {
  const params = await searchParams;
  const days = WINDOWS.includes(Number(params.days)) ? Number(params.days) : 60;
  const category = CATEGORIES.some((c) => c.id === params.category) ? params.category! : "all";
  const report = await agent.upskills(days);
  const missing = report.missing.filter((u) => category === "all" || u.category === category);
  const href = (d: number, c: string) => `/upskills?days=${d}${c === "all" ? "" : `&category=${c}`}`;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <header>
        <h1 className="text-h1">Upskills</h1>
        <p className="mt-1.5 text-ink-2">
          What the {report.jobsConsidered} job{report.jobsConsidered === 1 ? "" : "s"} WERA scored for you in the last {days} days ask for that your CV
          doesn&apos;t show yet — the skills worth learning first, and the ones you could already claim.
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

      {report.jobsConsidered === 0 ? (
        <Card className="p-5 text-ink-2">No scored jobs in this window yet. Run a search and this fills in from what those posts ask for.</Card>
      ) : null}

      {report.quickWins.length ? (
        <Card className="p-5">
          <h2 className="text-[16px] font-bold">Already on your CV — say so</h2>
          <p className="mt-1 text-small text-ink-2">
            Posts ask for these, and a line on your CV shows each one, but your skills list doesn&apos;t name them. Adding them is the quickest ATS gain there is —{" "}
            <Link href="/cv" className="underline hover:text-forest-700">add them on your CV</Link> if you&apos;d claim them.
          </p>
          <ul className="mt-3 space-y-2">
            {report.quickWins.map((w) => (
              <li key={w.term} className="text-[14px]">
                <span className="font-semibold">{w.term}</span>
                <span className="text-ink-2"> · {w.jobs} job{w.jobs === 1 ? "" : "s"}</span>
                {w.line ? <p className="text-small text-ink-2">“{w.line}”</p> : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <section aria-labelledby="missing-title" className="flex flex-col gap-3">
        <h2 id="missing-title" className="text-h2">Not on your CV yet</h2>
        {missing.map((u) => (
          <Card key={u.term} id={anchor(u.term)} className="scroll-mt-20 p-5">
            {/* Every spelling the posts used, so a pack's "Missing" link lands here whichever it had. */}
            {u.spellings
              .map(anchor)
              .filter((id, i, all) => id !== anchor(u.term) && all.indexOf(id) === i)
              .map((id) => (
                <span key={id} id={id} className="block scroll-mt-20" aria-hidden="true" />
              ))}
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-[17px] font-bold [overflow-wrap:anywhere]">{u.term}</h3>
              <span className="text-small text-ink-2">{TREND[u.trend]}</span>
            </div>
            <div className="mt-2 h-2 overflow-hidden rounded-none bg-mint-100" role="progressbar" aria-label={`${u.term}: share of jobs asking`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(u.share * 100)}>
              <div className="h-full rounded-none bg-accent" style={{ width: `${Math.max(2, Math.round(u.share * 100))}%` }} />
            </div>
            <p className="mt-1.5 text-small text-ink-2">
              {u.jobs} of {report.jobsConsidered} jobs ask for it ({Math.round(u.share * 100)}%) · {Math.round(u.requiredShare * 100)}% of them list it as required
            </p>
            {u.closeTo.length ? <p className="mt-1 text-small">You already have {u.closeTo.join(" and ")} — the closest way in.</p> : null}
            {u.examples.length ? (
              <ChipList className="mt-2">
                {u.examples.map((e) => (
                  <Chip key={e.id} variant="outline" title={e.title}>
                    {e.company}
                  </Chip>
                ))}
              </ChipList>
            ) : null}
          </Card>
        ))}
        {!missing.length && report.jobsConsidered ? <Card className="p-5 text-ink-2">Nothing missing in this category — your CV shows what these posts ask for.</Card> : null}
      </section>
    </div>
  );
}
