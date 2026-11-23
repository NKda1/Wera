"use client";

import type { Pack } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import type { Segment } from "./highlight";
import { optimiseView } from "./optimise";

/**
 * ATSC-10 (22 Nov): what the CV-for-ATS optimiser reworded on this redraft, each line before and
 * after with the keyword it added marked. "Undo this change" puts the original line back through
 * the per-line edit (G2-02), so it is then marked as the person's own.
 */
export function OptimiseCard({
  pack,
  editable,
  busy,
  onUndo,
}: {
  pack: Pack;
  editable: boolean;
  busy?: string;
  onUndo?: (target: string, text: string) => void;
}) {
  const view = optimiseView(pack);
  if (!view) return null;
  return (
    <Card className="p-5">
      <h2 className="text-[16px] font-bold">
        ATS optimised: <span className="tabular-nums">{view.from} → {view.to}</span>
      </h2>
      <p className="mt-1 text-small text-ink-2">
        {view.changes.length === 1 ? "One line was" : `${view.changes.length} lines were`} reworded to carry a keyword your CV already backs, from lines of the same role. Check each reads true.
      </p>
      <ul className="mt-3 space-y-4">
        {view.changes.map(({ change, state, target, after }) => (
          <li key={`${change.location}-${change.keyword}`} className="border-l-2 border-mint-100 pl-3">
            <p className="text-small font-semibold text-ink-2">
              Added <span className="text-forest-700">{change.keyword}</span>
              <span className="ml-1.5 font-mono text-tag">{change.sourceIds.join(" + ")}</span>
            </p>
            <p className="mt-1 text-[14px] text-ink-2 line-through decoration-ink-2/50 [overflow-wrap:anywhere]">
              <span className="sr-only">Before: </span>
              {change.before}
            </p>
            <p className="mt-1 text-[14px] [overflow-wrap:anywhere]">
              <span className="sr-only">After: </span>
              <Marked segments={after} />
            </p>
            {change.reason ? <p className="mt-0.5 text-small text-ink-2">{change.reason}</p> : null}
            {state === "applied" && target && editable && onUndo ? (
              <Button variant="secondary" className="mt-2" busy={busy === `edit-${target}`} onClick={() => onUndo(target, change.before)}>
                Undo this change
              </Button>
            ) : state === "undone" ? (
              <p className="mt-1 text-small text-ink-2">Undone: the original line is back.</p>
            ) : state === "edited" ? (
              <p className="mt-1 text-small text-ink-2">You&apos;ve rewritten this line since.</p>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Marked({ segments }: { segments: Segment[] }) {
  return (
    <>
      {segments.map((s, i) =>
        s.mark ? (
          <mark key={i} className={cn("bg-mint-100 px-0.5 text-forest-900 underline decoration-forest-700 decoration-2 underline-offset-2")}>
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}
