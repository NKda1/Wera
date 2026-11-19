"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Checkbox, Input } from "@/lib/ui/Field";
import { cn } from "@/lib/ui/cn";
import { SEARCH_FROM, buttonText, matches } from "./filters";

type Option = { id: string; label: string; count: number };

/** One filter group as a dropdown: a button naming the group (and what is picked), a popover of
 *  checkboxes under it, a search box once the list is long. Esc or an outside click closes it. */
export function FilterMenu({
  group,
  options,
  selected,
  onToggle,
  onClear,
}: {
  group: string;
  options: Option[];
  selected: string[];
  onToggle: (id: string) => void;
  onClear: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  // Pixels to slide the popover left so it stays on screen when its button sits near the right edge.
  const [shift, setShift] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const id = useId();
  // Whether the open came from the keyboard (a click event with no pointer has `detail` 0).
  const byKeyboard = useRef(false);

  const searchable = options.length > SEARCH_FROM;
  const q = query.trim();
  const shown = q ? options.filter((o) => matches(o.label, q)) : options;
  const chosen = options.filter((o) => selected.includes(o.id)).map((o) => o.label);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!wrap.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        close();
        button.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    // The search box when there is one, otherwise the first checkbox. On a touch screen the search
    // box would raise the keyboard over the list, so there the first checkbox gets focus instead.
    const typing = byKeyboard.current || window.matchMedia("(pointer: fine)").matches;
    const first = pop.current?.querySelector<HTMLElement>(typing ? "input" : "input[type=checkbox]");
    first?.focus();
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  // Left-aligned under the button, then nudged back inside a 16px gutter (a phone is 390px wide).
  useLayoutEffect(() => {
    if (!open || !pop.current || !button.current) return;
    const left = button.current.getBoundingClientRect().left;
    const width = pop.current.offsetWidth;
    const room = document.documentElement.clientWidth - 16;
    setShift(Math.max(16 - left, Math.min(0, room - (left + width))));
  }, [open]);

  // Clear unmounts itself (nothing is selected any more), so hand focus on first or it falls to <body>.
  const clear = () => {
    (pop.current?.querySelector<HTMLElement>("input") ?? button.current)?.focus();
    onClear();
  };

  const onPopKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const els = [...(pop.current?.querySelectorAll<HTMLElement>("input") ?? [])];
    const i = els.indexOf(document.activeElement as HTMLElement);
    els[Math.min(els.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))]?.focus();
  };

  return (
    <div ref={wrap} className="relative shrink-0">
      <button
        ref={button}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={(e) => {
          byKeyboard.current = e.detail === 0;
          if (open) close();
          else setOpen(true);
        }}
        className={cn(
          "inline-flex min-h-9 items-center gap-1.5 border px-3 text-[13px] font-semibold transition-colors duration-150 pointer-coarse:min-h-11",
          selected.length ? "border-forest-700 bg-accent text-white" : "border-line bg-surface text-ink hover:border-forest-700 hover:text-forest-700",
        )}
      >
        {buttonText(group, chosen)}
        <svg className="size-3.5 shrink-0" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open ? (
        <div
          ref={pop}
          id={id}
          role="dialog"
          aria-label={`${group} filters`}
          onKeyDown={onPopKey}
          style={{ left: shift }}
          className="absolute top-full z-30 mt-1.5 w-[min(320px,calc(100vw-32px))] animate-fade-in rounded-control border border-line bg-surface shadow-pop"
        >
          {searchable ? (
            <div className="p-2">
              <Input type="search" aria-label={`Search ${group}`} placeholder={`Search ${group.toLowerCase()}`} value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
          ) : null}
          <ul className="max-h-[min(60vh,420px)] overflow-y-auto px-3 py-1">
            {shown.map((o) => (
              <li key={o.id}>
                <Checkbox
                  className="py-2 pointer-coarse:min-h-11 pointer-coarse:items-center"
                  checked={selected.includes(o.id)}
                  onChange={() => onToggle(o.id)}
                  label={
                    <>
                      {o.label} <span className="font-normal text-ink-2 tabular-nums">{o.count}</span>
                    </>
                  }
                />
              </li>
            ))}
            {shown.length ? null : <li className="py-3 text-small text-ink-2">No matches</li>}
          </ul>
          {selected.length ? (
            <div className="border-t border-line px-3 py-1.5">
              <button type="button" onClick={clear} className="min-h-9 text-[13px] font-semibold text-forest-700 hover:underline pointer-coarse:min-h-11">
                Clear
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
