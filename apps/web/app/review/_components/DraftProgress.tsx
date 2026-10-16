import type { DraftStep } from "@/lib/types";
import { ProgressSteps, type StepState } from "@/lib/ui/ProgressSteps";

const STEPS: { step: DraftStep; label: string }[] = [
  { step: "cv", label: "Tailoring CV" },
  { step: "letter", label: "Cover letter" },
  { step: "ats", label: "ATS check" },
];

/** PERF-03: step-by-step progress while a pack is drafted. */
export function DraftProgress({ step }: { step: DraftStep }) {
  const at = STEPS.findIndex((s) => s.step === step);
  return (
    <ProgressSteps
      steps={STEPS.map((s, i): { label: string; state: StepState } => ({ label: s.label, state: i < at ? "done" : i === at ? "active" : "todo" }))}
    />
  );
}
