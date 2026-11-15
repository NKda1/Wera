"use client";

import { Pencil } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Button } from "@/lib/ui/Button";
import { cn } from "@/lib/ui/cn";

/** G2-02: edit any line in place. Saved edits are marked "edited by you". */
export function EditableText({
  value,
  editable,
  edited,
  onSave,
  label,
  render,
  multiline,
  extraActions,
}: {
  value: string;
  editable: boolean;
  edited?: boolean;
  onSave: (text: string) => Promise<boolean>;
  label: string;
  render?: (text: string) => ReactNode;
  multiline?: boolean;
  extraActions?: ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);

  if (editing) {
    return (
      <div className="w-full">
        <textarea
          aria-label={label}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={multiline ? 6 : 3}
          autoFocus
          className="w-full rounded-control border border-forest-700 bg-surface px-3 py-2 text-[15px] leading-relaxed ring-2 ring-forest-700/15 focus-visible:outline-none"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setDraft(value);
              setEditing(false);
            }
          }}
        />
        <div className="mt-2 flex flex-wrap gap-2">
          <Button
            busy={saving}
            onClick={async () => {
              setSaving(true);
              const ok = await onSave(draft);
              setSaving(false);
              if (ok) setEditing(false);
            }}
          >
            Save
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setDraft(value);
              setEditing(false);
            }}
          >
            Cancel
          </Button>
          {extraActions}
        </div>
      </div>
    );
  }

  const open = () => {
    setDraft(value);
    setEditing(true);
  };

  // Nothing written yet ("Write your answer"): the prompt itself is the button. Before, the only
  // way in was the pencil — and that is hidden until hovered, which a touch screen never does.
  if (editable && !value) {
    return (
      <button type="button" onClick={open} className="min-h-11 text-left font-semibold underline underline-offset-2 hover:no-underline" aria-label={`Edit ${label}`}>
        {render ? render(value) : value}
      </button>
    );
  }

  return (
    <span className="group/edit relative block">
      <span className={cn(edited && "rounded-none bg-amber-50/70")}>{render ? render(value) : value}</span>
      {edited ? <span className="ml-1.5 text-small whitespace-nowrap text-amber-700 italic">edited by you</span> : null}
      {editable ? (
        // Hidden until hovered only where something can hover (`pointer-fine`): on a touch
        // tablet it used to be invisible from 640px up and could only be tapped blind. The
        // `after:` box widens what a finger can hit to 40px without making the icon bigger — a
        // bigger icon sits in the line and stretched the last line of every paragraph. Even at
        // 28px it stood taller than the line; `-my-1` takes it back under the line height.
        <button
          type="button"
          onClick={open}
          className="relative -my-1 ml-1.5 inline-flex size-7 translate-y-1 items-center justify-center rounded-none text-ink-2 opacity-60 after:absolute after:-inset-1.5 after:content-[''] hover:bg-mint-100 hover:text-forest-900 hover:opacity-100 focus-visible:opacity-100 sm:pointer-fine:opacity-0 sm:pointer-fine:group-hover/edit:opacity-100 sm:pointer-fine:focus-visible:opacity-100"
          aria-label={`Edit ${label}`}
        >
          <Pencil className="size-3.5" />
        </button>
      ) : null}
    </span>
  );
}
