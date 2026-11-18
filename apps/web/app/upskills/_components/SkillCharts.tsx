"use client";

import { useRef, useState, type FocusEvent, type PointerEvent, type ReactNode } from "react";
import type { Coverage, SkillDemand } from "@/lib/types";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";

/** Where a skill the jobs ask for stands against the CV. One meaning per colour on every chart of
 *  the page, in this order, so a reader learns it once (`--color-viz-*` in globals.css). */
export const STATUS = {
  covered: { label: "On your skills list", color: "var(--color-viz-covered)" },
  quick_win: { label: "In a CV line, not your skills list", color: "var(--color-viz-quick)" },
  missing: { label: "Not on your CV", color: "var(--color-viz-missing)" },
} as const;

type Status = keyof typeof STATUS;
const ORDER: Status[] = ["covered", "quick_win", "missing"];

const pct = (n: number, of: number) => (of ? Math.round((n / of) * 100) : 0);
const askCount = (c: Coverage) => c.covered + c.quickWins + c.missing;
const valueOf = (c: Coverage, s: Status) => (s === "covered" ? c.covered : s === "quick_win" ? c.quickWins : c.missing);

/** The page's one legend: every chart below wears the same three colours. */
export function StatusLegend() {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-1.5 text-small text-ink-2" aria-label="Legend">
      {ORDER.map((s) => (
        <li key={s} className="flex items-center gap-2">
          <span className="inline-block size-3 rounded-[2px]" style={{ background: STATUS[s].color }} aria-hidden="true" />
          {STATUS[s].label}
        </li>
      ))}
    </ul>
  );
}

type Tip = { x: number; y: number; value: string; label: string };

/** One tooltip per chart, placed from the pointer or, on keyboard focus, from the mark itself.
 *  Values lead, labels follow; it never holds anything the table view doesn't. */
function useTip() {
  const box = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const place = (x: number, y: number, value: string, label: string) => {
    const r = box.current?.getBoundingClientRect();
    if (r) setTip({ x: x - r.left, y: y - r.top, value, label });
  };
  const on = (value: string, label: string) => ({
    onPointerMove: (e: PointerEvent) => place(e.clientX, e.clientY, value, label),
    onPointerLeave: () => setTip(null),
    onFocus: (e: FocusEvent<Element>) => {
      const m = e.currentTarget.getBoundingClientRect();
      place(m.left + m.width / 2, m.top, value, label);
    },
    onBlur: () => setTip(null),
  });
  const layer = tip ? (
    <div
      role="status"
      className="pointer-events-none absolute z-10 max-w-60 -translate-x-1/2 -translate-y-[calc(100%+10px)] border border-line bg-surface px-2.5 py-1.5 shadow-pop"
      style={{ left: tip.x, top: tip.y }}
    >
      <p className="text-[14px] font-semibold text-ink">{tip.value}</p>
      <p className="text-small text-ink-2">{tip.label}</p>
    </div>
  ) : null;
  return { box, on, layer };
}

/** A chart in a card, with its table-view twin one click away. */
function Frame({ id, title, caption, chart, table }: { id: string; title: string; caption: ReactNode; chart: ReactNode; table: ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <Card className="p-5" aria-labelledby={`${id}-title`}>
      <figure className="m-0">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id={`${id}-title`} className="text-[16px] font-bold">
            {title}
          </h2>
          <button type="button" onClick={() => setAsTable((t) => !t)} className="text-small font-semibold text-forest-700 hover:underline" aria-pressed={asTable}>
            {asTable ? "Show chart" : "Show as table"}
          </button>
        </div>
        <figcaption className="mt-1 text-small text-ink-2">{caption}</figcaption>
        <div className="mt-4">{asTable ? table : chart}</div>
      </figure>
    </Card>
  );
}

function StatusTable({ rows }: { rows: { label: string; coverage: Coverage }[] }) {
  return (
    <table className="w-full text-small">
      <thead className="text-left text-ink-2">
        <tr>
          <th className="py-1 pr-3 font-semibold"> </th>
          {ORDER.map((s) => (
            <th key={s} className="py-1 pr-3 font-semibold">
              {STATUS[s].label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="tabular-nums">
        {rows.map((r) => (
          <tr key={r.label} className="border-t border-line">
            <th className="py-1.5 pr-3 text-left font-semibold">{r.label}</th>
            {ORDER.map((s) => (
              <td key={s} className="py-1.5 pr-3">
                {valueOf(r.coverage, s)} ({pct(valueOf(r.coverage, s), askCount(r.coverage))}%)
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const R = 78; // the ring's centre line, in a 200-unit box
const RING = 22;
const GAP = 2 / R; // a 2px surface gap between segments, as an angle

function arc(from: number, to: number): string {
  const a = (t: number) => [100 + R * Math.cos(t), 100 + R * Math.sin(t)];
  const [x1, y1] = a(from);
  const [x2, y2] = a(to);
  return `M ${x1} ${y1} A ${R} ${R} 0 ${to - from > Math.PI ? 1 : 0} 1 ${x2} ${y2}`;
}

/** Part of the whole, at a glance: every skill the jobs ask for, once per job asking, by where it
 *  stands on the CV. Three segments, so a donut reads; the exact numbers are in the table view. */
export function CoverageDonut({ coverage, jobs }: { coverage: Coverage; jobs: number }) {
  const { box, on, layer } = useTip();
  const total = askCount(coverage);
  const shown = coverage.covered + coverage.quickWins;
  let at = -Math.PI / 2;
  const parts = ORDER.map((s) => {
    const share = total ? valueOf(coverage, s) / total : 0;
    const from = at;
    at += share * 2 * Math.PI;
    return { s, share, from, to: at };
  }).filter((p) => p.share > 0);
  return (
    <Frame
      id="coverage"
      title="What these jobs ask for, against your CV"
      caption={`Every skill the ${jobs} jobs name, counted once for each job that asks — ${total} asks in all.`}
      table={<StatusTable rows={[{ label: "All skills", coverage }]} />}
      chart={
        <div ref={box} className="relative flex flex-col items-center gap-4 sm:flex-row sm:items-center sm:gap-8">
          <svg viewBox="0 0 200 200" className="size-48 shrink-0" role="img" aria-label={`${pct(shown, total)}% of what these jobs ask for is on your CV`}>
            <circle cx="100" cy="100" r={R} fill="none" stroke="var(--color-mint-100)" strokeWidth={RING} />
            {parts.map((p) => {
              const whole = parts.length === 1;
              const value = `${valueOf(coverage, p.s)} asks · ${Math.round(p.share * 100)}%`;
              return whole ? (
                <circle key={p.s} cx="100" cy="100" r={R} fill="none" stroke={STATUS[p.s].color} strokeWidth={RING} tabIndex={0} {...on(value, STATUS[p.s].label)} />
              ) : (
                <path
                  key={p.s}
                  d={arc(p.from + GAP / 2, Math.max(p.from + GAP / 2, p.to - GAP / 2))}
                  fill="none"
                  stroke={STATUS[p.s].color}
                  strokeWidth={RING}
                  tabIndex={0}
                  className="cursor-default outline-none transition-opacity hover:opacity-80 focus-visible:opacity-80"
                  aria-label={`${STATUS[p.s].label}: ${value}`}
                  {...on(value, STATUS[p.s].label)}
                />
              );
            })}
            <text x="100" y="98" textAnchor="middle" className="fill-ink text-[44px] font-bold">
              {pct(shown, total)}%
            </text>
            <text x="100" y="124" textAnchor="middle" className="fill-ink-2 text-[12px]">
              on your CV
            </text>
          </svg>
          <ul className="flex w-full flex-col gap-2 text-small">
            {ORDER.map((s) => (
              <li key={s} className="flex items-baseline justify-between gap-3 border-b border-line pb-1.5 last:border-0">
                <span className="flex items-center gap-2">
                  <span className="inline-block size-3 shrink-0 rounded-[2px]" style={{ background: STATUS[s].color }} aria-hidden="true" />
                  {STATUS[s].label}
                </span>
                <span className="font-semibold text-ink tabular-nums">{pct(valueOf(coverage, s), total)}%</span>
              </li>
            ))}
          </ul>
          {layer}
        </div>
      }
    />
  );
}

const CATEGORY_NAMES: Record<string, string> = { hard_skill: "Skills", tool: "Tools", methodology: "Methods", other: "Other" };

/** Part of the whole per kind of skill: a 100% bar each, so where the gap sits reads across rows. */
export function CategoryBars({ rows }: { rows: (Coverage & { category: string })[] }) {
  const { box, on, layer } = useTip();
  const shown = rows.filter((r) => askCount(r) > 0);
  return (
    <Frame
      id="categories"
      title="Where the gap is"
      caption="Each kind of skill the jobs name, split by where it stands on your CV."
      table={<StatusTable rows={shown.map((r) => ({ label: CATEGORY_NAMES[r.category] ?? r.category, coverage: r }))} />}
      chart={
        <div ref={box} className="relative flex flex-col gap-4">
          {shown.map((r) => {
            const total = askCount(r);
            const parts = ORDER.map((s) => ({ s, n: valueOf(r, s) })).filter((p) => p.n > 0);
            const name = CATEGORY_NAMES[r.category] ?? r.category;
            return (
              <div key={r.category}>
                <div className="mb-1 flex items-baseline justify-between gap-2 text-small">
                  <span className="font-semibold text-ink">{name}</span>
                  <span className="text-ink-2 tabular-nums">
                    {pct(r.covered + r.quickWins, total)}% on your CV · {total} asks
                  </span>
                </div>
                <div className="flex h-3.5 gap-[2px]">
                  {parts.map((p, i) => (
                    <span
                      key={p.s}
                      tabIndex={0}
                      aria-label={`${name}, ${STATUS[p.s].label}: ${p.n} asks`}
                      className={cn("h-full outline-none transition-opacity hover:opacity-80 focus-visible:opacity-80", i === parts.length - 1 && "rounded-r-[4px]")}
                      style={{ width: `${(p.n / total) * 100}%`, background: STATUS[p.s].color }}
                      {...on(`${p.n} asks · ${pct(p.n, total)}%`, `${name} · ${STATUS[p.s].label}`)}
                    />
                  ))}
                </div>
              </div>
            );
          })}
          {layer}
        </div>
      }
    />
  );
}

/** The most-asked skills, longest first: bar length is the share of jobs asking, colour is where
 *  it stands on the CV — so the gaps that matter most are the long amber bars near the top. */
export function DemandBars({ demand, jobs }: { demand: SkillDemand[]; jobs: number }) {
  const { box, on, layer } = useTip();
  const top = Math.max(...demand.map((d) => d.share), 0.01);
  return (
    <Frame
      id="demand"
      title={`The ${demand.length} skills these jobs ask for most`}
      caption={`How many of the ${jobs} jobs name each one.`}
      table={
        <table className="w-full text-small">
          <thead className="text-left text-ink-2">
            <tr>
              <th className="py-1 pr-3 font-semibold">Skill</th>
              <th className="py-1 pr-3 font-semibold">Jobs asking</th>
              <th className="py-1 font-semibold">On your CV</th>
            </tr>
          </thead>
          <tbody>
            {demand.map((d) => (
              <tr key={d.term} className="border-t border-line">
                <th className="py-1.5 pr-3 text-left font-semibold [overflow-wrap:anywhere]">{d.term}</th>
                <td className="py-1.5 pr-3 tabular-nums">
                  {d.jobs} ({Math.round(d.share * 100)}%)
                </td>
                <td className="py-1.5">{STATUS[d.status].label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
      chart={
        <div ref={box} className="relative">
          <ul className="flex flex-col gap-2">
            {demand.map((d) => (
              <li key={d.term} className="grid grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)] items-center gap-3 sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)]">
                <span className="truncate text-small text-ink" title={d.term}>
                  {d.term}
                </span>
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    tabIndex={0}
                    aria-label={`${d.term}: ${d.jobs} of ${jobs} jobs, ${STATUS[d.status].label}`}
                    className="block h-3.5 shrink-0 rounded-r-[4px] outline-none transition-opacity hover:opacity-80 focus-visible:opacity-80"
                    style={{ width: `calc((100% - 3rem) * ${d.share / top})`, minWidth: 3, background: STATUS[d.status].color }}
                    {...on(`${d.jobs} of ${jobs} jobs · ${Math.round(d.share * 100)}%`, `${d.term} · ${STATUS[d.status].label}`)}
                  />
                  <span className="text-small text-ink-2 tabular-nums">{Math.round(d.share * 100)}%</span>
                </span>
              </li>
            ))}
          </ul>
          {layer}
        </div>
      }
    />
  );
}
