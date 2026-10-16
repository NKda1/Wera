"use client";

import { X } from "lucide-react";
import { useId, useState } from "react";
import { cn } from "./cn";

/** Editable chip list: Enter or comma adds, Backspace on an empty field removes the last chip. */
export function ChipInput({
  label,
  value,
  onChange,
  placeholder = "Add…",
  error,
  hint,
  max,
  variant = "mint",
  hideLabel,
  compact,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  error?: string;
  hint?: string;
  max?: number;
  variant?: "mint" | "outline";
  hideLabel?: boolean;
  compact?: boolean;
}) {
  const [draft, setDraft] = useState("");
  const id = useId();
  const full = max != null && value.length >= max;

  const add = (raw: string) => {
    const v = raw.trim().replace(/,$/, "");
    if (!v || full) return;
    if (!value.some((x) => x.toLowerCase() === v.toLowerCase())) onChange([...value, v]);
    setDraft("");
  };

  return (
    <div>
      <div className={cn("mb-1.5 flex items-baseline justify-between gap-2", hideLabel && "sr-only")}>
        <label htmlFor={id} className="text-[14px] font-semibold">
          {label}
        </label>
        {max != null ? (
          <span className={cn("text-small tabular-nums", full ? "text-amber-700" : "text-ink-2")}>
            {value.length} / {max}
          </span>
        ) : null}
      </div>
      <div
        className={cn(
          "flex flex-wrap items-center gap-1.5 rounded-control border bg-white px-2 focus-within:border-forest-700 focus-within:ring-2 focus-within:ring-forest-700/20",
          compact ? "min-h-9 py-1" : "min-h-11 py-1.5",
          error ? "border-red-700" : "border-line",
        )}
      >
        {value.map((v) => (
          <span
            key={v}
            className={cn(
              "inline-flex items-center gap-1 rounded-full py-0.5 pr-1 pl-2.5 text-small",
              variant === "mint" ? "bg-mint-100 text-ink" : "border border-line bg-white text-ink-2",
            )}
          >
            {v}
            <button
              type="button"
              onClick={() => onChange(value.filter((x) => x !== v))}
              className="inline-flex size-6 items-center justify-center rounded-full hover:bg-mint-200"
              aria-label={`Remove ${v}`}
            >
              <X className="size-3.5" />
            </button>
          </span>
        ))}
        <input
          id={id}
          value={draft}
          disabled={full}
          onChange={(e) => (e.target.value.endsWith(",") ? add(e.target.value) : setDraft(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(draft);
            } else if (e.key === "Backspace" && !draft && value.length) {
              onChange(value.slice(0, -1));
            }
          }}
          onBlur={() => add(draft)}
          placeholder={full ? "Limit reached" : placeholder}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-err` : hint ? `${id}-hint` : undefined}
          className="min-w-28 flex-1 bg-transparent px-1.5 py-1 text-[15px] outline-none placeholder:text-ink-2/70 focus-visible:outline-none"
        />
      </div>
      {error ? (
        <p id={`${id}-err`} role="alert" className="mt-1.5 text-small font-medium text-red-700">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="mt-1.5 text-small text-ink-2">
          {hint}
        </p>
      ) : null}
    </div>
  );
}
