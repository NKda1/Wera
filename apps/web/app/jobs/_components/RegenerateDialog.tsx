"use client";

// PRF-04: regenerating from the CV never overwrites silently. Every field that would change is
// shown — added chips with a +, removed chips struck through — and the person chooses "keep
// mine" or "use new" per field. Nothing is saved until they press Save.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiRequestError } from "@/lib/api";
import type { ListDiff, ProfileProposalResult, ScalarDiff, SearchProfile } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Chip, ChipList } from "@/lib/ui/Chip";
import { cn } from "@/lib/ui/cn";
import { Dialog } from "@/lib/ui/Dialog";
import { useToast } from "@/lib/ui/Toast";

const LABELS: Record<string, string> = {
  titles: "Target titles",
  skills: "Top skills",
  locations: "Cities",
  seniority: "Seniority",
  exclude: "Excluding",
};

const isList = (d: ListDiff | ScalarDiff): d is ListDiff => "added" in d;

/** The fields the model proposes. Where, remote, recency, boards and channels stay the person's
 *  own, and come back from the agent unchanged — except `locations`, offered only to a profile
 *  that names nowhere (`Store.generate_profile`). */
type MergeableField = "titles" | "skills" | "locations" | "seniority" | "exclude";

type Choice = "mine" | "new";

export function RegenerateDialog({ result, current, onClose }: { result: ProfileProposalResult; current: SearchProfile; onClose: () => void }) {
  const router = useRouter();
  const toast = useToast();
  const fields = Object.keys(result.diff);
  // Default to the proposal: the person asked for it. Each field can be switched back.
  const [choices, setChoices] = useState<Record<string, Choice>>(Object.fromEntries(fields.map((f) => [f, "new"])));
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      // Field by field: the proposal where they chose "use new", what they had where they didn't.
      // A field that isn't in the diff didn't change, so either side gives the same value.
      const pick = <K extends MergeableField>(field: K): SearchProfile[K] => (choices[field] === "mine" ? current[field] : result.proposed[field]);
      await api.putProfile({
        ...result.proposed,
        titles: pick("titles"),
        skills: pick("skills"),
        locations: pick("locations"),
        seniority: pick("seniority"),
        exclude: pick("exclude"),
      });
      toast({ tone: "ok", message: fields.length ? "Search profile saved. The next search uses it." : "Profile marked up to date with your CV." });
      router.refresh();
      onClose();
    } catch (err) {
      const message = err instanceof ApiRequestError || err instanceof Error ? err.message : "Couldn't save the profile.";
      toast({ tone: "error", message });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title="Regenerate from your CV"
      description={`Proposed from master CV v${result.cvVersion}. Choose what to take, field by field — where you search, remote and how recent stay as you set them. Nothing is saved until you press Save.`}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          {/* With nothing to change, saving still records the CV version it was checked against —
              which is the only thing that clears the "this profile came from v4" banner. The
              button used to be disabled here, so that banner could never be dismissed. */}
          <Button onClick={save} busy={saving}>
            {fields.length ? "Save profile" : "Mark up to date"}
          </Button>
        </>
      }
    >
      {!fields.length ? (
        <p className="text-ink-2">Your profile already matches your CV — there's nothing to change.</p>
      ) : (
        <ul className="grid gap-4">
          {fields.map((field) => {
            const change = result.diff[field];
            const choice = choices[field];
            return (
              <li key={field} className="rounded-card border border-line p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-[15px] font-bold">{LABELS[field] ?? field}</p>
                  <div className="inline-flex rounded-control border border-line bg-mint-50 p-1" role="radiogroup" aria-label={`${LABELS[field] ?? field}: which version to keep`}>
                    {(["mine", "new"] as const).map((option) => (
                      <button
                        key={option}
                        type="button"
                        role="radio"
                        aria-checked={choice === option}
                        onClick={() => setChoices((c) => ({ ...c, [field]: option }))}
                        className={cn(
                          "min-h-8 rounded-none px-3 text-small font-semibold pointer-coarse:min-h-11",
                          choice === option ? "bg-surface text-forest-900 shadow-sm" : "text-ink-2 hover:text-ink",
                        )}
                      >
                        {option === "mine" ? "Keep mine" : "Use new"}
                      </button>
                    ))}
                  </div>
                </div>

                {isList(change) ? (
                  <div className="mt-3 grid gap-2">
                    {change.added.length ? (
                      <ChipList>
                        {change.added.map((item) => (
                          <Chip key={item} variant="mint">
                            + {item}
                          </Chip>
                        ))}
                      </ChipList>
                    ) : null}
                    {change.removed.length ? (
                      <ChipList>
                        {change.removed.map((item) => (
                          <Chip key={item} variant="outline" className="line-through decoration-ink-2">
                            {item}
                          </Chip>
                        ))}
                      </ChipList>
                    ) : null}
                    {change.kept.length ? (
                      <p className="text-small text-ink-2">
                        Unchanged: {change.kept.join(", ")}
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="mt-3 text-[15px]">
                    <span className="text-ink-2 line-through">{change.from}</span> <span aria-hidden="true">→</span>{" "}
                    <span className="font-semibold">{change.to}</span>
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Dialog>
  );
}
