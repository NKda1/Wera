"use client";

import { useRef, useState, type CSSProperties, type FocusEvent, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import type { CategoryCoverage, Coverage, SkillDemand } from "@/lib/types";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import {
  CATEGORY_NAMES,
  STATUSES,
  asksOf,
  askCounts,
  importanceOf,
  mostlyPoor,
  shareOf,
  weighted,
  weightedTotal,
  type Status,
} from "@/lib/upskills";

/** Where a skill the jobs ask for stands against the CV. One meaning per colour on every chart of
 *  the page, in this order, so a reader learns it once (`--color-viz-*` in globals.css). */
export const STATUS = {
  covered: { label: "On your skills list", short: "on your skills list", color: "var(--color-viz-covered)" },
  quick_win: { label: "In a CV line, not your skills list", short: "in a CV line only", color: "var(--color-viz-quick)" },
  missing: { label: "Not on your CV", short: "not on your CV", color: "var(--color-viz-missing)" },
} as const;

const pct = (share: number) => Math.round(share * 100);
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const all = (show: Status[]) => show.length === STATUSES.length;

/** A mark's fill. Asks from jobs on the list are the status colour; asks from jobs the CV nearly
 *  matched (20 Nov) are the same colour hatched at 45° over a wash of it, so the source reads
 *  without a second hue — the status colours stay the only three — and without colour at all. */
export function fill(s: Status, near = false): CSSProperties {
  const c = STATUS[s].color;
  return near
    ? { background: `repeating-linear-gradient(135deg, ${c} 0 2px, color-mix(in oklab, ${c} 28%, var(--color-surface)) 2px 5px)` }
    : { background: c };
}

/** The page's one legend, and its status filter (20 Nov): every chart below wears the same three
 *  colours, and a click on one shows only that status — a second click shows them all again. With
 *  near misses in, it also keys the two sources. */
export function StatusLegend({ show, onStatus, near }: { show: Status[]; onStatus: (s: Status) => void; near: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-small text-ink-2">
      <ul className="flex flex-wrap gap-x-1.5 gap-y-1.5" aria-label="Show one status">
        {STATUSES.map((s) => {
          const on = show.includes(s);
          return (
            <li key={s}>
              <button
                type="button"
                onClick={() => onStatus(s)}
                aria-pressed={on && !all(show)}
                className={cn(
                  "flex items-center gap-2 border px-2 py-1 text-left transition-opacity pointer-coarse:min-h-10",
                  on && !all(show) ? "border-forest-700 bg-mint-100 text-ink" : "border-transparent hover:bg-mint-100",
                  !on && "opacity-50",
                )}
              >
                <span className="inline-block size-3 shrink-0 rounded-[2px]" style={fill(s)} aria-hidden="true" />
                {STATUS[s].label}
              </button>
            </li>
          );
        })}
      </ul>
      {near ? (
        <span className="flex flex-wrap gap-x-4 gap-y-1" aria-label="Sources">
          <span className="flex items-center gap-2">
            <span className="inline-block h-3 w-5 rounded-[2px]" style={fill("missing")} aria-hidden="true" /> Jobs on your list
          </span>
          <span className="flex items-center gap-2">
            <span className="inline-block h-3 w-5 rounded-[2px]" style={fill("missing", true)} aria-hidden="true" /> Jobs you nearly matched
          </span>
        </span>
      ) : null}
    </div>
  );
}

type Tip = { x: number; y: number; value: string; label: string; lines?: string[] };
type TipBody = Omit<Tip, "x" | "y">;

/** One tooltip per chart, placed from the pointer or, on keyboard focus, from the mark itself.
 *  Values lead, labels follow; it never holds anything the table view doesn't. */
function useTip() {
  const box = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const place = (x: number, y: number, body: TipBody) => {
    const r = box.current?.getBoundingClientRect();
    // Kept inside the chart, so a tip near an edge never makes the page scroll sideways.
    if (r) setTip({ ...body, x: Math.min(Math.max(x - r.left, 120), Math.max(r.width - 120, 120)), y: y - r.top });
  };
  const on = (body: TipBody) => ({
    onPointerMove: (e: PointerEvent) => place(e.clientX, e.clientY, body),
    onPointerLeave: () => setTip(null),
    onFocus: (e: FocusEvent<Element>) => {
      const m = e.currentTarget.getBoundingClientRect();
      place(m.left + m.width / 2, m.top, body);
    },
    onBlur: () => setTip(null),
  });
  const layer = tip ? (
    <div
      role="status"
      className="pointer-events-none absolute z-10 w-max max-w-60 -translate-x-1/2 -translate-y-[calc(100%+10px)] border border-line bg-surface px-2.5 py-1.5 shadow-pop"
      style={{ left: tip.x, top: tip.y }}
    >
      <p className="text-[14px] font-semibold text-ink">{tip.value}</p>
      <p className="text-small text-ink-2">{tip.label}</p>
      {tip.lines?.map((l) => (
        <p key={l} className="text-small text-ink-2">
          {l}
        </p>
      ))}
    </div>
  ) : null;
  return { box, on, layer };
}

/** Enter and Space on a mark that isn't a <button> (an SVG arc). */
const press = (act: () => void) => (e: KeyboardEvent) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    act();
  }
};

/** A chart in a card, with its table-view twin one click away. */
function Frame({ id, title, caption, chart, table }: { id: string; title: string; caption: ReactNode; chart: ReactNode; table: ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <Card className="min-w-0 p-5" aria-labelledby={`${id}-title`}>
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

/** "12 asks" or "12 on your list + 8 nearly matched". */
function askText(a: { list: number; near: number }, withNear: boolean) {
  return withNear ? `${a.list} on your list + ${a.near} nearly matched` : plural(a.list, "ask");
}

function StatusTable({ rows, weight, withNear }: { rows: { label: string; coverage: Coverage }[]; weight: number; withNear: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-small">
        <thead className="text-left text-ink-2">
          <tr>
            <th className="py-1 pr-3 font-semibold"> </th>
            {STATUSES.map((s) => (
              <th key={s} className="py-1 pr-3 font-semibold">
                {STATUS[s].label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {rows.map((r) => (
            <tr key={r.label} className="border-t border-line align-top">
              <th className="py-1.5 pr-3 text-left font-semibold">{r.label}</th>
              {STATUSES.map((s) => (
                <td key={s} className="py-1.5 pr-3">
                  {asksOf(r.coverage, s).list}
                  {withNear ? ` + ${asksOf(r.coverage, s).near} near` : ""} ({pct(shareOf(r.coverage, [s], weight))}%)
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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

/** What the donut's centre says for the statuses in view. */
function centreLabel(show: Status[]): { statuses: Status[]; label: string } {
  if (all(show)) return { statuses: ["covered", "quick_win"], label: "on your CV" };
  if (show.length === 1) return { statuses: show, label: STATUS[show[0]].short };
  if (!show.includes("covered")) return { statuses: show, label: "not on your skills list" };
  return { statuses: show, label: "in view" };
}

/** Part of the whole, at a glance: every skill the jobs ask for, once per job asking, by where it
 *  stands on the CV. Three segments, so a donut reads; each is a filter (20 Nov) — a click shows
 *  only that status on the page — and the exact numbers are in its tooltip and the table view.
 *  With near misses in, a near miss's ask counts `weight` (half) of one, as in the gap list. */
export function CoverageDonut({
  coverage,
  jobs,
  nearMisses,
  weight,
  show,
  onStatus,
}: {
  coverage: Coverage;
  jobs: number;
  nearMisses: number;
  weight: number;
  show: Status[];
  onStatus: (s: Status) => void;
}) {
  const { box, on, layer } = useTip();
  const withNear = nearMisses > 0;
  const total = weightedTotal(coverage, weight);
  const counts = askCounts(coverage);
  const centre = centreLabel(show);
  let at = -Math.PI / 2;
  const parts = STATUSES.map((s) => {
    const share = total ? weighted(coverage, s, weight) / total : 0;
    const from = at;
    at += share * 2 * Math.PI;
    return { s, share, from, to: at };
  }).filter((p) => p.share > 0);
  const tipFor = (s: Status): TipBody => {
    const a = asksOf(coverage, s);
    return {
      value: `${pct(shareOf(coverage, [s], weight))}% · ${plural(a.list + a.near, "ask")}`,
      label: STATUS[s].label,
      lines: withNear ? [`${a.list} from jobs on your list`, `${a.near} from jobs you nearly matched, counted half`] : undefined,
    };
  };
  return (
    <Frame
      id="coverage"
      title="What these jobs ask for, against your CV"
      caption={
        withNear
          ? `Every skill the jobs name, once for each job that asks: ${counts.list} asks from the ${jobs} jobs on your list and ${counts.near} from the ${nearMisses} you nearly matched, which count half. Click a status to show only it.`
          : `Every skill the ${jobs} jobs name, counted once for each job that asks — ${counts.list} asks in all. Click a status to show only it.`
      }
      table={<StatusTable rows={[{ label: "All skills", coverage }]} weight={weight} withNear={withNear} />}
      chart={
        <div ref={box} className="relative flex flex-col items-center gap-4 sm:flex-row sm:items-center sm:gap-8">
          <svg viewBox="0 0 200 200" className="size-48 shrink-0" role="group" aria-label={`${pct(shareOf(coverage, centre.statuses, weight))}% ${centre.label}`}>
            <circle cx="100" cy="100" r={R} fill="none" stroke="var(--color-mint-100)" strokeWidth={RING} />
            {parts.map((p) => {
              const tip = tipFor(p.s);
              const dim = !show.includes(p.s);
              const common = {
                fill: "none",
                stroke: STATUS[p.s].color,
                strokeWidth: RING,
                tabIndex: 0,
                role: "button",
                "aria-pressed": show.includes(p.s) && !all(show),
                "aria-label": `${STATUS[p.s].label}: ${tip.value}${tip.lines ? `, ${tip.lines.join(", ")}` : ""}. Show only this status.`,
                className: cn("cursor-pointer outline-none transition-opacity hover:opacity-80 focus-visible:opacity-80", dim && "opacity-30"),
                onClick: () => onStatus(p.s),
                onKeyDown: press(() => onStatus(p.s)),
                ...on(tip),
              };
              return parts.length === 1 ? (
                <circle key={p.s} cx="100" cy="100" r={R} {...common} />
              ) : (
                <path key={p.s} d={arc(p.from + GAP / 2, Math.max(p.from + GAP / 2, p.to - GAP / 2))} {...common} />
              );
            })}
            <text x="100" y="98" textAnchor="middle" className="pointer-events-none fill-ink text-[44px] font-bold">
              {pct(shareOf(coverage, centre.statuses, weight))}%
            </text>
            <text x="100" y="124" textAnchor="middle" className="pointer-events-none fill-ink-2 text-[12px]">
              {centre.label}
            </text>
          </svg>
          <ul className="flex w-full flex-col gap-1 text-small">
            {STATUSES.map((s) => {
              const a = asksOf(coverage, s);
              return (
                <li key={s} className="border-b border-line last:border-0">
                  <button
                    type="button"
                    onClick={() => onStatus(s)}
                    aria-pressed={show.includes(s) && !all(show)}
                    className={cn("flex w-full items-baseline justify-between gap-3 py-1.5 text-left hover:bg-mint-100 pointer-coarse:min-h-10", !show.includes(s) && "opacity-50")}
                  >
                    <span className="flex items-center gap-2">
                      <span className="inline-block size-3 shrink-0 rounded-[2px]" style={fill(s)} aria-hidden="true" />
                      <span>
                        {STATUS[s].label}
                        <span className="block text-ink-2 tabular-nums">{askText(a, withNear)}</span>
                      </span>
                    </span>
                    <span className="font-semibold text-ink tabular-nums">{pct(shareOf(coverage, [s], weight))}%</span>
                  </button>
                </li>
              );
            })}
          </ul>
          {layer}
        </div>
      }
    />
  );
}

/** Part of the whole per kind of skill: a 100% bar each, so where the gap sits reads across rows.
 *  Each status's near-miss asks are its hatched part, at half an ask. A click on a row (20 Nov)
 *  scopes the page to that kind of skill; a second click shows every kind again. */
export function CategoryBars({
  rows,
  weight,
  nearMisses,
  category,
  show,
  onCategory,
}: {
  rows: CategoryCoverage[];
  weight: number;
  nearMisses: number;
  category: string;
  show: Status[];
  onCategory: (c: string) => void;
}) {
  const { box, on, layer } = useTip();
  const withNear = nearMisses > 0;
  return (
    <Frame
      id="categories"
      title="Where the gap is"
      caption="Each kind of skill the jobs name, split by where it stands on your CV. Click one to show only it."
      table={<StatusTable rows={rows.map((r) => ({ label: CATEGORY_NAMES[r.category] ?? r.category, coverage: r }))} weight={weight} withNear={withNear} />}
      chart={
        <div ref={box} className="relative flex flex-col gap-2">
          {rows.map((r) => {
            const total = weightedTotal(r, weight);
            const counts = askCounts(r);
            const name = CATEGORY_NAMES[r.category] ?? r.category;
            const picked = category === r.category;
            const dim = category !== "all" && !picked;
            const parts = STATUSES.flatMap((s) => {
              const a = asksOf(r, s);
              return [
                { s, near: false, n: a.list, w: a.list },
                { s, near: true, n: a.near, w: a.near * weight },
              ].filter((p) => p.n > 0);
            });
            const summary = `${pct(shareOf(r, ["covered", "quick_win"], weight))}% on your CV`;
            return (
              <button
                key={r.category}
                type="button"
                onClick={() => onCategory(r.category)}
                aria-pressed={picked}
                aria-label={`${name}: ${summary}, ${STATUSES.map((s) => `${STATUS[s].short} ${pct(shareOf(r, [s], weight))}%`).join(", ")}. ${picked ? "Show every kind." : "Show only this kind."}`}
                className={cn(
                  "block w-full border border-transparent p-1.5 text-left outline-none transition-opacity hover:bg-mint-100 focus-visible:border-forest-700",
                  picked && "border-forest-700 bg-mint-100",
                  dim && "opacity-45",
                )}
                {...on({
                  value: `${name} · ${summary}`,
                  label: withNear ? `${counts.list} asks on your list + ${counts.near} nearly matched` : plural(counts.list, "ask"),
                  lines: STATUSES.map((s) => `${STATUS[s].label}: ${pct(shareOf(r, [s], weight))}%`),
                })}
              >
                <span className="mb-1 flex items-baseline justify-between gap-2 text-small">
                  <span className="font-semibold text-ink">{name}</span>
                  <span className="text-ink-2 tabular-nums">
                    {summary} · {withNear ? `${counts.list} + ${counts.near} asks` : plural(counts.list, "ask")}
                  </span>
                </span>
                <span className="flex h-3.5 gap-[2px]" aria-hidden="true">
                  {parts.map((p, i) => (
                    <span
                      key={`${p.s}-${p.near}`}
                      className={cn("h-full", i === parts.length - 1 && "rounded-r-[4px]", !show.includes(p.s) && "opacity-30")}
                      style={{ width: `${(p.w / total) * 100}%`, ...fill(p.s, p.near) }}
                    />
                  ))}
                </span>
              </button>
            );
          })}
          {layer}
        </div>
      }
    />
  );
}

/** The skills that matter most in these jobs, most first, coloured by where each stands on the CV.
 *
 * 20 Nov: a bar's length is how much the skill matters, not a bare count — each job weighs more
 * the more poorly the CV matched it (`upskills.lift`, up to twice; a skill you list weighs its
 * plain count) and a job you nearly matched half, hatched — so near-miss demand is visible and the
 * gaps poor matches keep naming stand out. The number beside it is always the plain count of jobs
 * asking. A bar for a skill you lack is a button: it jumps to the skill's row below. */
export function DemandBars({
  demand,
  jobs,
  nearMisses,
  highlight,
  onSkill,
}: {
  demand: SkillDemand[];
  jobs: number;
  nearMisses: number;
  highlight?: string | null;
  onSkill: (d: SkillDemand) => void;
}) {
  const { box, on, layer } = useTip();
  const withNear = nearMisses > 0;
  const top = Math.max(...demand.map(importanceOf), 0.01);
  const tipFor = (d: SkillDemand): TipBody => {
    const lines: string[] = [];
    if (withNear) lines.push(`${d.jobs} on your list · ${d.nearMisses} you nearly matched (count half)`);
    if (d.status !== "covered" && d.poor) lines.push(`${d.poor} of ${d.jobs + d.nearMisses} matched you poorly — weighted up`);
    if (d.status === "missing") lines.push("Click to see it in the table below");
    if (d.status === "quick_win") lines.push("Click to see the CV line that shows it");
    return {
      value: withNear ? `${plural(d.jobs + d.nearMisses, "job")} ask` : `${d.jobs} of ${jobs} jobs · ${pct(d.share)}%`,
      label: `${d.term} · ${STATUS[d.status].label}`,
      lines,
    };
  };
  return (
    <Frame
      id="demand"
      title={`The ${demand.length} skill${demand.length === 1 ? "" : "s"} that matter most in these jobs`}
      caption={
        <>
          The number is how many jobs ask{withNear ? " (on your list + nearly matched)" : ""}. The bar is how much each matters: a job counts more the
          more poorly your CV matched it, up to twice{withNear ? ", and a job you nearly matched half (hatched)" : ""}. Click a gap to find it below.
        </>
      }
      table={
        <div className="overflow-x-auto">
          <table className="w-full text-small">
            <thead className="text-left text-ink-2">
              <tr>
                <th className="py-1 pr-3 font-semibold">Skill</th>
                <th className="py-1 pr-3 font-semibold">Jobs on your list</th>
                {withNear ? <th className="py-1 pr-3 font-semibold">Nearly matched</th> : null}
                <th className="py-1 pr-3 font-semibold">Weight</th>
                <th className="py-1 font-semibold">On your CV</th>
              </tr>
            </thead>
            <tbody>
              {demand.map((d) => (
                <tr key={d.term} className="border-t border-line align-top">
                  <th className="py-1.5 pr-3 text-left font-semibold [overflow-wrap:anywhere]">{d.term}</th>
                  <td className="py-1.5 pr-3 tabular-nums">
                    {d.jobs} ({pct(d.share)}%)
                  </td>
                  {withNear ? <td className="py-1.5 pr-3 tabular-nums">{d.nearMisses}</td> : null}
                  <td className="py-1.5 pr-3 tabular-nums">
                    {importanceOf(d).toFixed(1)}
                    {d.status !== "covered" && mostlyPoor(d) ? <span className="block text-ink-2">mostly poor matches</span> : null}
                  </td>
                  <td className="py-1.5">{STATUS[d.status].label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      }
      chart={
        <div ref={box} className="relative">
          <ul className="flex flex-col gap-0.5">
            {demand.map((d) => {
              const tip = tipFor(d);
              const target = d.status !== "covered";
              const body = (
                <>
                  <span className="truncate text-small text-ink" title={d.term}>
                    {d.term}
                    {d.status !== "covered" && mostlyPoor(d) ? (
                      <span className="sr-only"> (mostly from jobs you matched poorly)</span>
                    ) : null}
                  </span>
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="flex h-3.5 min-w-0 gap-[2px]" style={{ width: `calc((100% - 4rem) * ${importanceOf(d) / top})`, minWidth: 3 }} aria-hidden="true">
                      {d.importance > 0 ? (
                        <span
                          className={cn("h-full", d.nearImportance > 0 ? "" : "rounded-r-[4px]")}
                          style={{ flexGrow: d.importance, flexBasis: 0, minWidth: 2, ...fill(d.status) }}
                        />
                      ) : null}
                      {d.nearImportance > 0 ? (
                        <span className="h-full rounded-r-[4px]" style={{ flexGrow: d.nearImportance, flexBasis: 0, minWidth: 2, ...fill(d.status, true) }} />
                      ) : null}
                    </span>
                    <span className="shrink-0 text-small text-ink-2 tabular-nums">{withNear ? `${d.jobs} + ${d.nearMisses}` : d.jobs}</span>
                  </span>
                </>
              );
              const cls = cn(
                "grid w-full grid-cols-[minmax(0,7.5rem)_minmax(0,1fr)] items-center gap-3 px-1 py-1 text-left outline-none sm:grid-cols-[minmax(0,10rem)_minmax(0,1fr)]",
                "hover:bg-mint-100 focus-visible:bg-mint-100 focus-visible:ring-2 focus-visible:ring-forest-700",
                highlight === d.term && "bg-mint-100",
              );
              const label = `${d.term}: ${tip.value}, ${STATUS[d.status].label}${tip.lines?.length ? `. ${tip.lines.join(". ")}` : ""}`;
              return (
                <li key={d.term}>
                  {target ? (
                    <button type="button" className={cls} aria-label={label} onClick={() => onSkill(d)} {...on(tip)}>
                      {body}
                    </button>
                  ) : (
                    <div className={cls} tabIndex={0} role="img" aria-label={label} {...on(tip)}>
                      {body}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {layer}
        </div>
      }
    />
  );
}
