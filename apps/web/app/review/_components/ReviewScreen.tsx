"use client";

import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatAgo, formatDate, formatLocation } from "@/lib/format";
import type { ApplicationWithJob, MasterCV } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { Dialog } from "@/lib/ui/Dialog";
import { Help, Label, Textarea } from "@/lib/ui/Field";
import { Pill } from "@/lib/ui/Pill";
import { Tabs } from "@/lib/ui/Tabs";
import { useToast } from "@/lib/ui/Toast";
import { DRAFT_TOTAL_MS } from "@/lib/drafting";
import { AnswerPack, CvPaper, LetterPaper } from "./Documents";
import { DraftProgress } from "./DraftProgress";
import { ReviewRail } from "./ReviewRail";
import type { CvLines } from "./SourceTag";

type Tab = "cv" | "letter" | "answers";

const NOTE_IDEAS = ["Make it shorter", "More about Python", "Focus on the agent work", "Keep it to one page"];

export function ReviewScreen({ initial, cv, lines, vendor }: { initial: ApplicationWithJob; cv: MasterCV; lines: CvLines; vendor?: string }) {
  const router = useRouter();
  const toast = useToast();
  const [app, setApp] = useState(initial);
  const [tab, setTab] = useState<Tab>("cv");
  const [asking, setAsking] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [busy, setBusy] = useState<string>();

  useEffect(() => setApp(initial), [initial]);

  // While drafting, poll until the pack is ready, then refresh so the nav badge updates.
  useEffect(() => {
    if (app.status !== "drafting") return;
    const t = setInterval(async () => {
      try {
        const next = await api.getApplication(app.id);
        setApp(next);
        if (next.status !== "drafting") router.refresh();
      } catch {
        /* keep polling */
      }
    }, 700);
    return () => clearInterval(t);
  }, [app.status, app.id, router]);

  const pack = app.pack;
  const editable = app.status === "ready_for_review";
  const approved = ["approved", "submitted", "interview", "offer"].includes(app.status);
  const answersCount = pack?.answers.length ?? 0;
  const missingSourceIds = pack
    ? [...new Set([...pack.cv.sections.flatMap((s) => s.items.flatMap((i) => i.bullets.flatMap((b) => b.sourceIds))), ...pack.letter.paragraphs.flatMap((p) => p.sourceIds)])].filter(
        (id) => !lines[id],
      )
    : [];
  const readyAt = app.draftStartedAt ? new Date(new Date(app.draftStartedAt).getTime() + DRAFT_TOTAL_MS).toISOString() : app.createdAt;

  const run = async (key: string, fn: () => Promise<ApplicationWithJob>, ok?: string) => {
    setBusy(key);
    try {
      const next = await fn();
      setApp((a) => ({ ...a, ...next }));
      if (ok) toast({ tone: "ok", message: ok });
      router.refresh();
      return true;
    } catch (err) {
      toast({ tone: "error", message: err instanceof Error ? err.message : "That didn't work." });
      return false;
    } finally {
      setBusy(undefined);
    }
  };

  const save = (kind: "cv" | "letter" | "answers") => (target: string, text: string) =>
    run(`edit-${target}`, () => api.editDocument(`${app.id}.${kind}`, { target, text }), "Saved. Marked as edited by you.");

  const toggleLine = async (action: "add" | "remove", bulletId: string) => {
    await run(`line-${bulletId}`, () => api.editDocument(`${app.id}.cv`, { target: action, bulletId }), action === "add" ? `Added ${bulletId} back.` : `Removed ${bulletId}. It's listed under "Left out".`);
  };

  return (
    <div className="mx-auto max-w-[1180px]">
      <Link href="/review" className="inline-flex min-h-8 items-center gap-1.5 text-small font-semibold text-forest-700 hover:text-forest-900">
        <ArrowLeft className="size-4" aria-hidden="true" />
        Review
      </Link>

      <header className="mt-3 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <StatusPill app={app} />
          <h1 className="mt-3 text-[30px] leading-[1.1] font-bold tracking-[-0.02em] sm:text-h1">{app.job.title}</h1>
          <p className="mt-1.5 text-[15px] text-ink-2">
            {app.job.company} · {formatLocation(app.job)} · fit {app.job.fitTotal.toFixed(1)} ·{" "}
            {app.status === "drafting" ? "drafting now" : `drafted ${formatAgo(readyAt)}`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {editable ? (
            <>
              <Button variant="ghost" onClick={() => setDiscarding(true)}>
                Discard
              </Button>
              <Button variant="secondary" onClick={() => setAsking(true)}>
                Request changes
              </Button>
              <Button busy={busy === "approve"} onClick={() => run("approve", () => api.approvePack(app.id), "Pack approved. Downloads are unlocked.")}>
                Approve pack
              </Button>
            </>
          ) : app.status === "approved" ? (
            <>
              {app.job.applyUrl ? (
                <a href={app.job.applyUrl} target="_blank" rel="noreferrer noopener" className="inline-flex min-h-11 items-center gap-2 rounded-control border border-line bg-white px-5 text-[15px] font-semibold text-forest-900 hover:bg-mint-50">
                  Apply on the employer&apos;s site
                  <ExternalLink className="size-4" aria-hidden="true" />
                </a>
              ) : null}
              <Button
                busy={busy === "submit"}
                onClick={() => run("submit", () => api.patchApplication(app.id, { status: "submitted" }), "Marked as submitted. Follow-up reminder set for 7 days.")}
              >
                I&apos;ve submitted it
              </Button>
            </>
          ) : null}
        </div>
      </header>

      {app.status === "drafting" || !pack ? (
        <Card className="mt-6 p-6">
          <h2 className="text-h2">Drafting your pack</h2>
          <p className="mt-1 text-ink-2">{app.changeNote ? `Redrafting with your note: “${app.changeNote}”` : "Tailored CV, then cover letter, then an ATS check against the job."}</p>
          <div className="mt-4">{app.draft ? <DraftProgress step={app.draft.step} /> : null}</div>
        </Card>
      ) : (
        <>
          <div className="mt-6">
            <Tabs<Tab>
              label="Pack documents"
              idPrefix="pack"
              value={tab}
              onChange={setTab}
              tabs={[
                { id: "cv", label: "Tailored CV" },
                { id: "letter", label: "Cover letter" },
                { id: "answers", label: `Answer pack · ${answersCount}` },
              ]}
            />
          </div>
          <div className="mt-5 grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div role="tabpanel" id={`pack-panel-${tab}`} aria-labelledby={`pack-tab-${tab}`} className="min-w-0">
              {tab === "cv" ? (
                <CvPaper doc={pack.cv} cv={cv} lines={lines} editable={editable} onSave={save("cv")} onToggleLine={toggleLine} />
              ) : tab === "letter" ? (
                <LetterPaper letter={pack.letter} name={cv.person.name} lines={lines} editable={editable} onSave={save("letter")} />
              ) : (
                <AnswerPack answers={pack.answers} lines={lines} editable={editable} onSave={save("answers")} vendor={vendor} />
              )}
            </div>
            <aside aria-label="Checks">
              <ReviewRail pack={pack} appId={app.id} approved={approved} cvVersion={cv.version} missingSourceIds={missingSourceIds} />
            </aside>
          </div>
        </>
      )}

      {asking ? (
        <RequestChanges
          onClose={() => setAsking(false)}
          onSubmit={async (note) => {
            const ok = await run("regen", () => api.regenerate(app.id, note), "Redrafting with your note…");
            if (ok) setAsking(false);
          }}
          busy={busy === "regen"}
        />
      ) : null}

      <Dialog
        open={discarding}
        onClose={() => setDiscarding(false)}
        size="sm"
        title="Discard this pack?"
        description="The job is marked rejected and this pack is deleted. Your master CV isn't touched."
        footer={
          <>
            <Button variant="secondary" onClick={() => setDiscarding(false)}>
              Keep it
            </Button>
            <Button
              variant="danger"
              onClick={async () => {
                setDiscarding(false);
                if (await run("discard", () => api.patchApplication(app.id, { status: "discarded" }), "Pack discarded.")) router.push("/review");
              }}
            >
              Discard
            </Button>
          </>
        }
      />
    </div>
  );
}

function StatusPill({ app }: { app: ApplicationWithJob }) {
  switch (app.status) {
    case "ready_for_review":
      return <Pill tone="amber">Gate 2 · needs your approval</Pill>;
    case "drafting":
      return <Pill tone="green">Drafting</Pill>;
    case "approved":
      return <Pill tone="green">Approved · ready to send</Pill>;
    case "submitted":
      return <Pill tone="green">Submitted {formatDate(app.submittedAt)}</Pill>;
    case "interview":
      return <Pill tone="green">Interviewing</Pill>;
    case "offer":
      return <Pill tone="green">Offer</Pill>;
    default:
      return <Pill tone="neutral">{app.status.replace(/_/g, " ")}</Pill>;
  }
}

function RequestChanges({ onClose, onSubmit, busy }: { onClose: () => void; onSubmit: (note: string) => void; busy: boolean }) {
  const [note, setNote] = useState("");
  return (
    <Dialog
      open
      onClose={onClose}
      title="Request changes"
      description="WERA redrafts the CV and letter with your note. It can still only use lines from your master CV."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => onSubmit(note)} disabled={!note.trim()} busy={busy}>
            Redraft
          </Button>
        </>
      }
    >
      <Label htmlFor="change-note">What should change?</Label>
      <Textarea id="change-note" rows={4} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Lead with the agent work and keep it to one page." autoFocus />
      <Help>Mention a skill from your CV to pull in lines about it.</Help>
      <div className="mt-3 flex flex-wrap gap-2">
        {NOTE_IDEAS.map((idea) => (
          <button
            key={idea}
            type="button"
            onClick={() => setNote((n) => (n ? `${n.trim()} ${idea}.` : `${idea}.`))}
            className="min-h-9 rounded-full border border-line bg-white px-3 text-small font-semibold text-ink-2 hover:bg-mint-50 hover:text-ink"
          >
            {idea}
          </button>
        ))}
      </div>
    </Dialog>
  );
}
