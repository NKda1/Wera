"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatAgo, formatLocation, pct } from "@/lib/format";
import type { ApplicationWithJob } from "@/lib/types";
import { Button, ButtonLink } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { Pill } from "@/lib/ui/Pill";
import { ScoreRing } from "@/lib/ui/ScoreRing";
import { useToast } from "@/lib/ui/Toast";
import { DraftProgress } from "./DraftProgress";

type Item = ApplicationWithJob & { coverage?: number };

export function ReviewList({ apps }: { apps: Item[] }) {
  const router = useRouter();
  const toast = useToast();
  const [cancelling, setCancelling] = useState<string>();
  const drafting = apps.filter((a) => a.status === "drafting" || a.status === "changes_requested");
  const ready = apps.filter((a) => a.status === "ready_for_review");
  const approved = apps.filter((a) => a.status === "approved");

  // Drafts advance on the server; refresh until they land.
  useEffect(() => {
    if (!drafting.length) return;
    const t = setInterval(() => router.refresh(), 900);
    return () => clearInterval(t);
  }, [drafting.length, router]);

  return (
    <div className="mx-auto max-w-[920px]">
      {ready.length ? <Pill tone="amber">Gate 2 · {ready.length === 1 ? "1 pack needs" : `${ready.length} packs need`} your approval</Pill> : <Pill tone="green">Gate 2 · nothing waiting</Pill>}
      <h1 className="mt-3 text-[34px] leading-[1.05] font-bold tracking-[-0.025em] sm:text-display">Review</h1>
      <p className="mt-2 text-small text-ink-2">Every pack is built only from your master CV. Read it, edit anything, then approve it before you use it.</p>

      {drafting.length ? (
        <Section title="Drafting">
          {drafting.map((a) => (
            <Card key={a.id} className="flex flex-wrap items-center justify-between gap-4 p-5">
              <div className="min-w-0">
                <h3 className="text-h2">{a.job.title}</h3>
                <p className="text-small text-ink-2">
                  {a.job.company} · {formatLocation(a.job)}
                  {a.changeNote ? ` · redrafting with your note` : ""}
                </p>
                <div className="mt-3">{a.draft ? <DraftProgress step={a.draft.step} /> : null}</div>
              </div>
              <Button
                variant="secondary"
                busy={cancelling === a.id}
                onClick={async () => {
                  setCancelling(a.id);
                  try {
                    await api.patchApplication(a.id, { status: "discarded" });
                    toast({ tone: "ok", message: `Cancelled. ${a.job.title} is back on your shortlist.` });
                    router.refresh();
                  } catch (err) {
                    toast({ tone: "error", message: err instanceof Error ? err.message : "Couldn't cancel." });
                  } finally {
                    setCancelling(undefined);
                  }
                }}
              >
                Cancel
              </Button>
            </Card>
          ))}
        </Section>
      ) : null}

      <Section title="Needs your approval">
        {ready.map((a) => (
          <PackRow key={a.id} app={a} primary />
        ))}
        {!ready.length ? (
          <Card className="px-6 py-10 text-center">
            <p className="text-h2">Nothing to review</p>
            <p className="mx-auto mt-2 max-w-md text-ink-2">Approve a job on your shortlist and WERA drafts a tailored CV, cover letter and ATS report for it.</p>
            <ButtonLink href="/jobs" className="mt-5">
              Go to the shortlist
            </ButtonLink>
          </Card>
        ) : null}
      </Section>

      {approved.length ? (
        <Section title="Approved · ready to send">
          {approved.map((a) => (
            <PackRow key={a.id} app={a} />
          ))}
        </Section>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-8">
      <h2 className="mb-3 text-[13px] font-bold tracking-[0.08em] text-forest-700 uppercase">{title}</h2>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  );
}

function PackRow({ app, primary }: { app: Item; primary?: boolean }) {
  // When drafting began. Phase 1 added a fixed 6s to guess when it finished; the graph
  // takes as long as the models take, so the honest figure is the one that is stored.
  const draftedAt = app.draftStartedAt ?? app.createdAt;
  return (
    <Card className="flex flex-wrap items-center gap-4 p-5">
      <ScoreRing score={app.job.fitTotal} size="sm" />
      <div className="min-w-0 flex-1">
        <h3 className="text-h2">
          <Link href={`/review/${app.id}`} className="hover:text-forest-700">
            {app.job.title}
          </Link>
        </h3>
        <p className="text-small text-ink-2">
          {app.job.company} · {formatLocation(app.job)} · {app.status === "approved" ? `approved ${formatAgo(app.approvedAt)}` : `drafted ${formatAgo(draftedAt)}`}
          {app.coverage != null ? ` · ATS ${pct(app.coverage)}` : ""}
        </p>
      </div>
      <ButtonLink href={`/review/${app.id}`} variant={primary ? "primary" : "secondary"}>
        {primary ? "Review pack" : "Open pack"}
      </ButtonLink>
    </Card>
  );
}
