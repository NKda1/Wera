import { scoreBand } from "../scoring";
import { cn } from "./cn";

const ring = {
  strong: "border-forest-700",
  good: "border-leaf-500",
  weak: "border-leaf-500/45",
  attention: "border-amber-500",
} as const;

/** Fit score in a ring: forest for strong, leaf for good, pale leaf for weaker, amber when you need to look. */
export function ScoreRing({ score, roughOnly, size = "md", label }: { score: number; roughOnly?: boolean; size?: "sm" | "md"; label?: string }) {
  const band = scoreBand(score, roughOnly);
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full border-[3.5px] bg-white font-bold tabular-nums text-ink",
        size === "md" ? "size-16 text-[22px]" : "size-11 text-[15px] border-[3px]",
        ring[band],
      )}
      role="img"
      aria-label={label ?? `Fit ${score.toFixed(1)} out of 10${roughOnly ? ", rough title match" : ""}`}
    >
      {score.toFixed(1)}
    </span>
  );
}
