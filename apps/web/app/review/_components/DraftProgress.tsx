import type { DraftStep } from "@/lib/types";
import { ProgressBar, ProgressSteps, type StepState } from "@/lib/ui/ProgressSteps";

/** The steps the application graph runs, in order, with what each one is actually doing.
 *
 * Keep this list in step with `DRAFT_ORDER` in the agent's store, which is the order the graph
 * runs. The `hint` is the half that was missing: `step` alone is one word per stage, and the
 * writing-tier calls behind "Tailoring CV" and "Cover letter" take tens of seconds each, so a
 * four-word checklist with no further detail stops changing for long enough to read as stuck. */
const STEPS: { step: DraftStep; label: string; hint: string }[] = [
  { step: "cv", label: "Tailoring CV", hint: "choosing which of your lines to show" },
  { step: "letter", label: "Cover letter", hint: "writing, then checking every claim against your CV" },
  { step: "ats", label: "ATS check", hint: "keyword coverage and page count" },
  { step: "answers", label: "Application answers", hint: "drafting from your CV, citing each line" },
];

/** PERF-03: step-by-step progress while a pack is drafted. */
export function DraftProgress({ step, progress }: { step: DraftStep; progress?: number }) {
  // `findIndex` returns -1 for a step this list doesn't know, which renders every step as
  // "waiting" — a blank indicator rather than a wrong one.
  const at = Math.max(
    STEPS.findIndex((s) => s.step === step),
    0,
  );
  return (
    <div className="flex flex-col gap-2.5">
      {/* The bar is derived server-side (`Store._draft`) and was never rendered, so drafting had
          a checklist and no sense of how far through it was. */}
      {progress !== undefined ? <ProgressBar value={progress} label="Drafting progress" /> : null}
      <ProgressSteps
        steps={STEPS.map((s, i): { label: string; state: StepState } => ({
          label: s.label,
          state: i < at ? "done" : i === at ? "active" : "todo",
        }))}
      />
      <p className="text-small text-ink-2">{STEPS[at].hint}</p>
    </div>
  );
}
