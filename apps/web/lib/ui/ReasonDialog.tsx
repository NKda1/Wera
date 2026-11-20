"use client";

import { useState } from "react";
import type { ReasonCode } from "@/lib/api";
import { Button } from "./Button";
import { FilterChip } from "./Chip";
import { Dialog } from "./Dialog";
import { Label, Textarea } from "./Field";

// MATCH-11: each chip is sent as its code, and anything typed as a note beside it — the dialog
// used to send one or the other, and only the code is something WERA can learn from.
export const REJECT_REASONS: { code: ReasonCode; label: string }[] = [
  { code: "wrong_level", label: "Wrong level" },
  { code: "wrong_location", label: "Wrong location" },
  { code: "not_my_field", label: "Not my field" },
  { code: "pay_too_low", label: "Pay too low" },
  { code: "applied_elsewhere", label: "Already applied elsewhere" },
];

const LEARNS =
  "A reason is optional. WERA learns from it: jobs like this move down your list, and it may suggest a change to your profile.";

/** "No" to a job, with an optional reason: a Gate 1 reject, or a pack discarded at Gate 2. */
export function ReasonDialog({
  title,
  description = LEARNS,
  action,
  danger = false,
  onClose,
  onConfirm,
}: {
  title: string;
  description?: string;
  action: string;
  danger?: boolean;
  onClose: () => void;
  onConfirm: (reasonCode?: ReasonCode, note?: string) => void;
}) {
  const [reason, setReason] = useState<ReasonCode>();
  const [other, setOther] = useState("");
  return (
    <Dialog
      open
      onClose={onClose}
      size="sm"
      title={title}
      description={description}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "dark"} onClick={() => onConfirm(reason, other.trim() || undefined)}>
            {action}
          </Button>
        </>
      }
    >
      <fieldset>
        <legend className="mb-2 text-[14px] font-semibold">Reason</legend>
        <div className="flex flex-wrap gap-2">
          {REJECT_REASONS.map((r) => (
            <FilterChip key={r.code} active={reason === r.code} onClick={() => setReason(reason === r.code ? undefined : r.code)}>
              {r.label}
            </FilterChip>
          ))}
        </div>
      </fieldset>
      <div className="mt-4">
        <Label htmlFor="reason-other">Something else</Label>
        <Textarea id="reason-other" rows={2} value={other} onChange={(e) => setOther(e.target.value)} className="min-h-0" />
      </div>
    </Dialog>
  );
}
