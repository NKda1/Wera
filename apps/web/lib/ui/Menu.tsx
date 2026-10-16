"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { cn } from "./cn";

/** A small disclosure menu: click to open, Esc or outside click to close, arrow keys move. */
export function Menu({
  trigger,
  items,
  align = "end",
  label,
  className,
}: {
  trigger: (props: { open: boolean; toggle: () => void; id: string }) => ReactNode;
  items: { label: string; hint?: string; onSelect: () => void }[];
  align?: "start" | "end";
  label: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        (wrap.current?.querySelector("[aria-haspopup]") as HTMLElement | null)?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    (wrap.current?.querySelector('[role="menuitem"]') as HTMLElement | null)?.focus();
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const onMenuKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const els = [...(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const i = els.indexOf(document.activeElement as HTMLElement);
    els[(i + (e.key === "ArrowDown" ? 1 : els.length - 1)) % els.length]?.focus();
  };

  return (
    <div ref={wrap} className={cn("relative", className)}>
      {trigger({ open, toggle: () => setOpen((o) => !o), id })}
      {open ? (
        <div
          id={id}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKey}
          className={cn(
            "absolute z-30 mt-1.5 min-w-44 animate-fade-in rounded-control border border-line bg-white p-1 shadow-pop",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
              className="flex min-h-11 w-full items-center justify-between gap-4 rounded-md px-3 text-left text-[15px] hover:bg-mint-100 focus-visible:bg-mint-100"
            >
              <span>{item.label}</span>
              {item.hint ? <span className="text-small text-ink-2">{item.hint}</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
