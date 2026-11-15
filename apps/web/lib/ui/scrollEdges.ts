"use client";

// Sideways-scrolling rows — the phone nav, tabs, the jobs filter chips — hide their scrollbar, so
// the fade at an edge is the only sign there is more. A fixed mask faded the last item even with
// the row scrolled to its end, and faded a row that fitted; this fades only an edge that has
// something past it.

import { useEffect, useState, type CSSProperties, type RefObject } from "react";

const FADE = "24px";

/** Which edges of a horizontal scroller have content beyond them. `key` re-measures when the
 *  contents change without the scroller itself resizing (a chip added, a tab renamed). */
export function useScrollEdges(ref: RefObject<HTMLElement | null>, key?: unknown): CSSProperties | undefined {
  const [edges, setEdges] = useState({ start: false, end: false });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const start = el.scrollLeft > 1;
      const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
      setEdges((e) => (e.start === start && e.end === end ? e : { start, end }));
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const seen = new ResizeObserver(update);
    seen.observe(el);
    for (const child of el.children) seen.observe(child);
    return () => {
      el.removeEventListener("scroll", update);
      seen.disconnect();
    };
  }, [ref, key]);

  if (!edges.start && !edges.end) return undefined;
  const mask = `linear-gradient(to right, ${edges.start ? "transparent" : "black"}, black ${FADE}, black calc(100% - ${FADE}), ${edges.end ? "transparent" : "black"})`;
  return { maskImage: mask, WebkitMaskImage: mask };
}

/** Scrolls `item` into view inside the horizontal scroller `box`, and nothing else.
 *  `scrollIntoView` would also scroll the page to bring the row on screen — on a phone, that
 *  jumped a screen whose tabs sit below the fold down to them on load. */
export function revealInline(box: HTMLElement | null, item: HTMLElement | null, margin = 16) {
  if (!box || !item) return;
  const left = item.getBoundingClientRect().left - box.getBoundingClientRect().left + box.scrollLeft;
  const right = left + item.offsetWidth;
  if (left - margin < box.scrollLeft) box.scrollLeft = Math.max(0, left - margin);
  else if (right + margin > box.scrollLeft + box.clientWidth) box.scrollLeft = right + margin - box.clientWidth;
}
