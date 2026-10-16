"use client";

import { useRef } from "react";
import { cn } from "./cn";

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
  idPrefix,
}: {
  tabs: { id: T; label: string }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
  idPrefix: string;
}) {
  const list = useRef<HTMLDivElement>(null);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = tabs.findIndex((t) => t.id === value);
    const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
    onChange(next.id);
    list.current?.querySelector<HTMLElement>(`#${idPrefix}-tab-${next.id}`)?.focus();
  };
  return (
    <div ref={list} role="tablist" aria-label={label} onKeyDown={onKey} className="scrollbar-none flex gap-6 overflow-x-auto border-b border-line">
      {tabs.map((t) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            id={`${idPrefix}-tab-${t.id}`}
            role="tab"
            type="button"
            aria-selected={active}
            aria-controls={`${idPrefix}-panel-${t.id}`}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.id)}
            className={cn(
              "-mb-px min-h-11 border-b-2 px-1 pb-2.5 text-[15px] whitespace-nowrap transition-colors",
              active ? "border-forest-700 font-semibold text-forest-900" : "border-transparent text-ink-2 hover:text-ink",
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
