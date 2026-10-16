"use client";

import { useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/ui/cn";

export type CvLines = Record<string, { text: string; where: string }>;

/** G2-01: every line shows its master-CV source; click to see the original. */
export function SourceTag({ id, lines, edited }: { id: string; lines: CvLines; edited?: boolean }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const popId = useId();
  const line = lines[id];

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => !wrap.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span ref={wrap} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls={open ? popId : undefined}
        aria-label={`Source ${id}: show the original line`}
        className={cn(
          "inline-flex min-h-6 items-center rounded-md px-1.5 font-mono text-tag font-semibold transition-colors",
          line ? "bg-mint-100 text-forest-900 hover:bg-mint-200" : "bg-red-50 text-red-700",
          open && "bg-mint-200",
        )}
      >
        {id}
      </button>
      {open ? (
        <span
          id={popId}
          role="dialog"
          aria-label={`Original line ${id}`}
          className="absolute top-full right-0 z-30 mt-1.5 block w-[min(340px,80vw)] animate-fade-in rounded-control border border-line bg-white p-3.5 text-left text-small leading-relaxed shadow-pop"
        >
          {line ? (
            <>
              <span className="block font-semibold text-forest-900">Master CV · {line.where}</span>
              <span className="mt-1 block text-ink">{line.text}</span>
              {edited ? <span className="mt-2 block text-amber-700">You edited this line in the pack. The master CV is unchanged.</span> : null}
            </>
          ) : (
            <span className="block text-red-700">This id is no longer in your master CV. The line was removed after this pack was drafted.</span>
          )}
        </span>
      ) : null}
    </span>
  );
}
