import type { ComponentProps } from "react";
import { cn } from "./cn";

export function Card({ className, ...props }: ComponentProps<"section">) {
  return <section className={cn("rounded-card border border-line bg-white shadow-card", className)} {...props} />;
}

export function CardHeader({ title, description, action, id }: { title: string; description?: React.ReactNode; action?: React.ReactNode; id?: string }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 id={id} className="text-[18px] font-bold tracking-[-0.01em]">
          {title}
        </h2>
        {description ? <p className="mt-0.5 text-small text-ink-2">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

export function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={cn("text-small text-ink-2", className)}>{children}</p>;
}
