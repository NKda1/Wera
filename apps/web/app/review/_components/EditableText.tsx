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
          className="w-full rounded-control border border-forest-700 bg-white px-3 py-2 text-[15px] leading-relaxed ring-2 ring-forest-700/15 focus-visible:outline-none"
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

  return (
    <span className="group/edit relative block">
      <span className={cn(edited && "rounded-sm bg-amber-50/70")}>{render ? render(value) : value}</span>
      {edited ? <span className="ml-1.5 text-small whitespace-nowrap text-amber-700 italic">edited by you</span> : null}
      {editable ? (
        <button
          type="button"
          onClick={() => {
            setDraft(value);
            setEditing(true);
          }}
          className="ml-1.5 inline-flex size-7 translate-y-1 items-center justify-center rounded-md text-ink-2 opacity-60 hover:bg-mint-100 hover:text-forest-900 hover:opacity-100 focus-visible:opacity-100 sm:opacity-0 sm:group-hover/edit:opacity-100"
          aria-label={`Edit ${label}`}
        >
          <Pencil className="size-3.5" />
        </button>
      ) : null}
    </span>
  );
}
