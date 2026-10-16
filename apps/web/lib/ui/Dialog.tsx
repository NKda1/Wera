"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";
import { cn } from "./cn";

/** Native <dialog> as a modal: the browser handles focus trapping, Esc and inertness. */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      className={cn(
        "m-auto max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] overflow-hidden rounded-card border border-line bg-white p-0 shadow-pop",
        size === "sm" ? "max-w-md" : size === "md" ? "max-w-xl" : "max-w-3xl",
      )}
    >
      {open ? (
        <div className="flex max-h-[calc(100dvh-34px)] flex-col">
          <div className="flex items-start justify-between gap-4 border-b border-line px-6 pt-5 pb-4">
            <div>
              <h2 id={titleId} className="text-h2">
                {title}
              </h2>
              {description ? <div className="mt-1 text-small text-ink-2">{description}</div> : null}
            </div>
            <button
              type="button"
              onClick={onClose}
              className="-mt-1 -mr-2 inline-flex size-11 shrink-0 items-center justify-center rounded-control text-ink-2 hover:bg-mint-100 hover:text-ink"
              aria-label="Close"
            >
              <X className="size-5" />
            </button>
          </div>
          <div className="overflow-y-auto px-6 py-5">{children}</div>
          {footer ? <div className="flex flex-wrap justify-end gap-2 border-t border-line bg-mint-50 px-6 py-4">{footer}</div> : null}
        </div>
      ) : null}
    </dialog>
  );
}
