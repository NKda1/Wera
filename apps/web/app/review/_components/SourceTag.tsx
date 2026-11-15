"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { cn } from "@/lib/ui/cn";

export type CvLines = Record<string, { text: string; where: string }>;

/** G2-01: every line shows its master-CV source; click to see the original. */
export function SourceTag({ id, lines, edited }: { id: string; lines: CvLines; edited?: boolean }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const popId = useId();
  const line = lines[id];
  // Where the popover's left edge goes, relative to the tag. Right-aligned to the tag as before,
  // then kept 8px inside the screen: anchored `right-0` alone, a tag near the left edge — every
  // CV bullet's on a phone, every letter paragraph's — opened it ~190px off-screen to the left,
  // where it couldn't be scrolled to.
  const [left, setLeft] = useState<number>();

  useLayoutEffect(() => {
    if (!open || !wrap.current) return;
    const tag = wrap.current.getBoundingClientRect();
    const width = Math.min(340, window.innerWidth * 0.8);
    const at = Math.min(Math.max(8, tag.right - width), window.innerWidth - width - 8);
    setLeft(at - tag.left);
  }, [open]);

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
          "inline-flex min-h-6 items-center rounded-none px-1.5 font-mono text-tag font-semibold transition-colors pointer-coarse:min-h-8 pointer-coarse:px-2",
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
          style={{ left }}
          className="absolute top-full z-30 mt-1.5 block w-[min(340px,80vw)] animate-fade-in rounded-control border border-line bg-surface p-3.5 text-left text-small leading-relaxed shadow-pop"
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
