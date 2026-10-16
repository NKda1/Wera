import type { ReactNode } from "react";
import { cn } from "./cn";

type Variant = "mint" | "outline" | "missing" | "source";

const variants: Record<Variant, string> = {
  mint: "rounded-full bg-mint-100 px-2.5 py-1 text-small text-ink",
  outline: "rounded-full border border-line bg-white px-2.5 py-[3px] text-small text-ink-2",
  missing: "rounded-full bg-amber-50 px-2.5 py-1 text-small text-amber-700",
  source: "rounded-md bg-mint-100 px-1.5 py-0.5 font-mono text-tag font-semibold text-forest-900",
};

export function Chip({ variant = "mint", className, title, children }: { variant?: Variant; className?: string; title?: string; children: ReactNode }) {
  return (
    <span title={title} className={cn("inline-flex items-center gap-1 whitespace-nowrap", variants[variant], className)}>
      {children}
    </span>
  );
}

export function ChipList({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("flex flex-wrap gap-1.5", className)}>{children}</div>;
}
