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
import { JobLinks } from "@/lib/ui/JobLinks";
import { Pill } from "@/lib/ui/Pill";
import { ReasonDialog } from "@/lib/ui/ReasonDialog";
import { Tabs } from "@/lib/ui/Tabs";
import { useToast } from "@/lib/ui/Toast";
import { cn } from "@/lib/ui/cn";
import { CompanyPanel } from "./CompanyPanel";
import { CvFileEditor } from "./CvFileEditor";
import { AnswerPack, CvPaper, LetterPaper } from "./Documents";
import { DraftProgress } from "./DraftProgress";
import { ReviewRail } from "./ReviewRail";
import type { CvLines } from "./SourceTag";

type Tab = "cv" | "letter" | "answers" | "company";
type CvView = "lines" | "file";

/** The note `Store.rewrite_letter` leaves on an application while its letter is redrafted alone. */
const LETTER_REWRITE = "Rewriting the letter with the company brief";

const NOTE_IDEAS = ["Make it shorter", "More about Python", "Focus on the agent work", "Keep it to one page"];

export function ReviewScreen({ initial, cv, lines, vendor }: { initial: ApplicationWithJob; cv: MasterCV; lines: CvLines; vendor?: string }) {
  const router = useRouter();
  const toast = useToast();
  const [app, setApp] = useState(initial);
  const [tab, setTab] = useState<Tab>("cv");
  const [cvView, setCvView] = useState<CvView>("lines");
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
    }, 1500);
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
  // When drafting began. Phase 1 added a fixed 6s to guess when it finished; the graph
  // takes as long as the models take, so the honest figure is the one that is stored.
  const draftedAt = app.draftStartedAt ?? app.createdAt;

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

  // `@container`: laid out by the width this screen has. Keyed to `lg:`, the checks rail arrived
  // with the sidebar at 1024px and left the document ~316px; it now joins at 896px of content.
  return (
    <div className="@container mx-auto max-w-[1180px]">
      <Link href="/review" className="inline-flex min-h-8 items-center gap-1.5 text-small font-semibold text-forest-700 hover:text-forest-900 pointer-coarse:min-h-11">
        <ArrowLeft className="size-4" aria-hidden="true" />
        Review
      </Link>

      <header className="mt-3 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <StatusPill app={app} />
          <h1 className="mt-3 text-[30px] leading-[1.1] font-bold tracking-[-0.02em] sm:text-h1">{app.job.title}</h1>
          <p className="mt-1.5 text-[15px] text-ink-2">
            {app.job.company} · {formatLocation(app.job)} · fit {app.job.fitTotal.toFixed(1)} ·{" "}
            {app.status === "drafting" ? "drafting now" : `drafted ${formatAgo(draftedAt)}`}
          </p>
          <JobLinks job={app.job} className="mt-2" />
        </div>
        {/* On a narrow screen the primary action comes first and full width; it used to land
            alone on a second row under Discard. First in the markup too, so it is first to tab
            to; `@xl:order-last` puts it back at the end of the row where there is room. */}
        <div className="flex flex-wrap gap-2 @max-xl:w-full">
          {editable ? (
            <>
              <Button
                busy={busy === "approve"}
                onClick={() => run("approve", () => api.approvePack(app.id), "Pack approved. Downloads are unlocked.")}
                className="@max-xl:w-full @xl:order-last"
              >
                Approve pack
              </Button>
              <Button variant="ghost" onClick={() => setDiscarding(true)} className="@max-xl:flex-1">
                Discard
              </Button>
              <Button variant="secondary" onClick={() => setAsking(true)} className="@max-xl:flex-1">
                Request changes
              </Button>
            </>
          ) : app.status === "approved" ? (
            <>
              {app.job.applyUrl ? (
                <a
                  href={app.job.applyUrl}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex min-h-11 items-center justify-center gap-2 rounded-control border border-line bg-surface px-5 text-[15px] font-semibold text-forest-900 hover:bg-mint-50 @max-xl:w-full"
                >
                  Apply on the employer&apos;s site
                  <ExternalLink className="size-4" aria-hidden="true" />
                </a>
              ) : null}
              <Button
                busy={busy === "submit"}
                onClick={() => run("submit", () => api.patchApplication(app.id, { status: "submitted" }), "Marked as submitted. Follow-up reminder set for 7 days.")}
                className="@max-xl:w-full"
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
          <p className="mt-1 text-ink-2">
            {/* The letter-only rewrite (`Store.rewrite_letter`) leaves its own note; it isn't one the person wrote. */}
            {app.changeNote === LETTER_REWRITE
              ? "Rewriting the cover letter with the company brief. The CV stays as it is."
              : app.changeNote
                ? `Redrafting with your note: “${app.changeNote}”`
                : "Tailored CV, then cover letter, then an ATS check against the job."}
          </p>
          <div className="mt-4">{app.draft ? <DraftProgress step={app.draft.step} progress={app.draft.progress} /> : null}</div>
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
                { id: "company", label: "Company" },
              ]}
            />
          </div>
          <div className="mt-5 grid items-start gap-5 @4xl:grid-cols-[minmax(0,1fr)_320px]">
            <div role="tabpanel" id={`pack-panel-${tab}`} aria-labelledby={`pack-tab-${tab}`} className="min-w-0">
              {tab === "cv" ? (
                <>
                  <CvViewSwitch value={cvView} onChange={setCvView} />
                  {cvView === "file" ? (
                    <CvFileEditor
                      appId={app.id}
                      onSaved={(next) => {
                        setApp((a) => ({ ...a, ...next }));
                        router.refresh();
                      }}
                    />
                  ) : (
                    <CvPaper
                      doc={pack.cv}
                      cv={cv}
                      lines={lines}
                      editable={editable}
                      rewritten={Boolean(pack.cvFile?.editedByUser)}
                      onOpenFile={() => setCvView("file")}
                      onSave={save("cv")}
                      onToggleLine={toggleLine}
                    />
                  )}
                </>
              ) : tab === "company" ? (
                <CompanyPanel
                  app={app}
                  editable={editable}
                  busy={busy === "brief" ? "learn" : busy === "rewrite" ? "rewrite" : undefined}
                  onLearn={() => run("brief", () => api.companyBrief(app.id), `Read what ${app.job.company} says about itself.`)}
                  onRewrite={() => run("rewrite", () => api.rewriteLetter(app.id), "Rewriting the letter with the company brief…")}
                />
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
          rewritten={Boolean(pack?.cvFile?.editedByUser)}
          onClose={() => setAsking(false)}
          onSubmit={async (note) => {
            const ok = await run("regen", () => api.regenerate(app.id, note), "Redrafting with your note…");
            if (ok) setAsking(false);
          }}
          busy={busy === "regen"}
        />
      ) : null}

      {discarding ? (
        <ReasonDialog
          title="Discard this pack?"
          description="The job is marked rejected and this pack is deleted. Your master CV isn't touched. A reason is optional; WERA learns from it, as from a reject."
          action="Discard"
          danger
          onClose={() => setDiscarding(false)}
          onConfirm={async (reasonCode, reason) => {
            setDiscarding(false);
            if (await run("discard", () => api.patchApplication(app.id, { status: "discarded", reasonCode, reason }), "Pack discarded.")) router.push("/review");
          }}
        />
      ) : null}
    </div>
  );
}

/** The tailored CV line by line, with each line's source; or the RenderCV file the PDF is made from. */
function CvViewSwitch({ value, onChange }: { value: CvView; onChange: (v: CvView) => void }) {
  const views: [CvView, string][] = [
    ["lines", "Lines and sources"],
    ["file", "CV file and design"],
  ];
  return (
    <div role="group" aria-label="How to show the CV" className="mb-4 flex flex-wrap gap-2">
      {views.map(([id, label]) => (
        <button
          key={id}
          type="button"
          aria-pressed={value === id}
          onClick={() => onChange(id)}
          className={cn(
            "inline-flex min-h-9 items-center border px-3 text-[13px] font-semibold transition-colors duration-150 pointer-coarse:min-h-11",
            value === id ? "border-forest-700 bg-accent text-white" : "border-line bg-surface text-ink hover:border-forest-700 hover:text-forest-700",
          )}
        >
          {label}
        </button>
      ))}
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

function RequestChanges({ onClose, onSubmit, busy, rewritten }: { onClose: () => void; onSubmit: (note: string) => void; busy: boolean; rewritten: boolean }) {
  const [note, setNote] = useState("");
  return (
    <Dialog
      open
      onClose={onClose}
      title="Request changes"
      description={
        rewritten
          ? "WERA redrafts the CV and letter with your note, from lines in your master CV. Your own version of the CV file is replaced by the redraft; its theme stays."
          : "WERA redrafts the CV and letter with your note. It can still only use lines from your master CV."
      }
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
            className="min-h-9 rounded-none border border-line bg-surface px-3 text-small font-semibold text-ink-2 hover:bg-mint-50 hover:text-ink"
          >
            {idea}
          </button>
        ))}
      </div>
    </Dialog>
  );
}
