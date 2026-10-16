import type { ReactNode } from "react";
import { cn } from "./cn";

export type Tone = "green" | "amber" | "red" | "neutral";

const tones: Record<Tone, { pill: string; dot: string }> = {
  green: { pill: "bg-mint-100 text-forest-900", dot: "bg-forest-700" },
  amber: { pill: "bg-amber-50 text-amber-700", dot: "bg-amber-500" },
  red: { pill: "bg-red-50 text-red-700", dot: "bg-red-700" },
  neutral: { pill: "bg-white text-ink-2 border border-line", dot: "bg-ink-2" },
};

/** Status pill: "Gate 1 · waiting for you", "Needs key", "Run failed". */
export function Pill({ tone = "green", dot = true, className, children }: { tone?: Tone; dot?: boolean; className?: string; children: ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-small font-semibold", tones[tone].pill, className)}>
      {dot ? <span className={cn("size-1.5 rounded-full", tones[tone].dot)} aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
