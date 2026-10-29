import type { DraftStep } from "@/lib/types";
import { ProgressSteps, type StepState } from "@/lib/ui/ProgressSteps";

const STEPS: { step: DraftStep; label: string }[] = [
  { step: "cv", label: "Tailoring CV" },
  { step: "letter", label: "Cover letter" },
  { step: "ats", label: "ATS check" },
  { step: "answers", label: "Application answers" },
];

/** PERF-03: step-by-step progress while a pack is drafted. */
export function DraftProgress({ step }: { step: DraftStep }) {
  // `findIndex` returns -1 for a step this list doesn't know, which renders every step as
  // "waiting" — a blank indicator rather than a wrong one. Keep this list in step with
  // `DRAFT_ORDER` in the agent's store, which is the order the graph runs.
  const at = Math.max(
    STEPS.findIndex((s) => s.step === step),
    0,
  );
  return (
    <ProgressSteps
      steps={STEPS.map((s, i): { label: string; state: StepState } => ({ label: s.label, state: i < at ? "done" : i === at ? "active" : "todo" }))}
    />
  );
}
