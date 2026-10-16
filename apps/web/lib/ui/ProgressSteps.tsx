import { Check } from "lucide-react";
import { cn } from "./cn";
import { Spinner } from "./Button";

export type StepState = "done" | "active" | "todo";

export function ProgressSteps({ steps, className }: { steps: { label: string; state: StepState; detail?: string }[]; className?: string }) {
  return (
    <ol className={cn("flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:gap-x-5", className)}>
      {steps.map((s) => (
        <li key={s.label} className="flex items-center gap-2 text-small">
          <span
            className={cn(
              "inline-flex size-5 items-center justify-center rounded-full",
              s.state === "done" && "bg-forest-700 text-white",
              s.state === "active" && "bg-mint-100 text-forest-700",
              s.state === "todo" && "border border-line bg-white",
            )}
            aria-hidden="true"
          >
            {s.state === "done" ? <Check className="size-3" strokeWidth={3} /> : s.state === "active" ? <Spinner className="size-3" /> : null}
          </span>
          <span className={cn(s.state === "todo" ? "text-ink-2" : "font-semibold text-ink")}>
            {s.label}
            <span className="sr-only">{s.state === "done" ? " (done)" : s.state === "active" ? " (in progress)" : " (waiting)"}</span>
          </span>
          {s.detail ? <span className="text-ink-2">{s.detail}</span> : null}
        </li>
      ))}
    </ol>
  );
}

export function ProgressBar({ value, label }: { value: number; label: string }) {
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-mint-100" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value * 100)}>
      <div className="h-full rounded-full bg-forest-700 transition-[width] duration-500" style={{ width: `${Math.round(value * 100)}%` }} />
    </div>
  );
}
