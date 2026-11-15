"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import type { SuggestionList } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card, CardHeader } from "@/lib/ui/Card";
import { useToast } from "@/lib/ui/Toast";

/** MATCH-13: profile changes the person's Gate 1 decisions point at. Nothing changes until they
 *  accept one (PRF-04); a dismissed one isn't offered again. Hidden when there is nothing to say. */
export function LearningCard({ suggestions }: { suggestions: SuggestionList }) {
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState<string>();
  if (!suggestions.items.length) return null;

  const act = async (id: string, accept: boolean) => {
    setBusy(id);
    try {
      if (accept) await api.acceptSuggestion(id);
      else await api.dismissSuggestion(id);
      toast({ tone: "ok", message: accept ? "Your search profile is updated. The next search uses it." : "WERA won't suggest that again." });
      router.refresh();
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't save that." });
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <Card className="p-5" aria-labelledby="learning-title">
      <CardHeader
        id="learning-title"
        title="WERA noticed"
        description={`From your last ${suggestions.decisions} decision${suggestions.decisions === 1 ? "" : "s"}. Nothing changes unless you accept it.`}
      />
      <ul className="mt-4 divide-y divide-line">
        {suggestions.items.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
            <p className="min-w-0 flex-1 text-[15px] text-ink">{s.message}</p>
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => act(s.id, false)} disabled={busy !== undefined}>
                Dismiss
              </Button>
              <Button onClick={() => act(s.id, true)} busy={busy === s.id} disabled={busy !== undefined && busy !== s.id}>
                Accept
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
