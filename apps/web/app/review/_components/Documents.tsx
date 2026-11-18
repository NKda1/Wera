"use client";

import { Check, Copy, Minus, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";
import { roleDates, year } from "@/lib/format";
import type { AnswerItem, CoverLetter, MasterCV, TailoredCV } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { cn } from "@/lib/ui/cn";
import { useToast } from "@/lib/ui/Toast";
import { EditableText } from "./EditableText";
import { SourceTag, type CvLines } from "./SourceTag";

type Save = (target: string, text: string) => Promise<boolean>;

export function CvPaper({
  doc,
  cv,
  lines,
  editable: reviewing,
  rewritten = false,
  onOpenFile,
  onSave,
  onToggleLine,
}: {
  doc: TailoredCV;
  cv: MasterCV;
  lines: CvLines;
  editable: boolean;
  /** The person rewrote the CV file, which is what the downloads use now; line edits wait. */
  rewritten?: boolean;
  onOpenFile?: () => void;
  onSave: Save;
  onToggleLine: (action: "add" | "remove", bulletId: string) => Promise<void>;
}) {
  const roles = new Map([...cv.experience, ...cv.projects].map((r) => [r.id, r]));
  const omitted = doc.omittedBulletIds.filter((id) => lines[id]);
  const editable = reviewing && !rewritten;

  return (
    <div className="space-y-4">
      {rewritten ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-control bg-amber-50 px-4 py-3 text-[14px] text-amber-700">
          <p className="min-w-0 flex-1">
            You rewrote this CV in its file, and the PDF and Word downloads use your version. This is WERA&apos;s draft, kept with its sources; line editing waits until you discard your edits.
          </p>
          {onOpenFile ? (
            <Button variant="secondary" onClick={onOpenFile}>
              Open the CV file
            </Button>
          ) : null}
        </div>
      ) : null}
      <Card className="px-5 py-7 sm:px-10 sm:py-10">
        <article aria-label="Tailored CV" className="text-[15px] leading-relaxed">
          <h2 className="text-[26px] font-bold tracking-[-0.01em]">{cv.person.name}</h2>
          <p className="text-small text-ink-2">[email] · [phone] · {cv.person.location} — contact details are added on your machine after generation</p>
          <hr className="mt-3 mb-5 border-t-2 border-forest-700" />

          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <EditableText value={doc.summary.text} editable={editable} edited={doc.summary.editedByUser} onSave={(t) => onSave("summary", t)} label="summary" multiline />
            </div>
          </div>

          {doc.sections.map((section, si) => (
            <section key={section.kind} className="mt-6">
              <h3 className="mb-2 text-[13px] font-bold tracking-[0.08em] text-forest-700 uppercase">{section.kind === "experience" ? "Experience" : "Projects"}</h3>
              {section.items.map((item, ii) => {
                const role = roles.get(item.roleId);
                return (
                  <div key={item.roleId} className="mb-4">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-4">
                      <p className="font-bold">
                        {role ? `${role.title} — ${role.org}${role.location ? `, ${role.location}` : ""}` : item.roleId}
                      </p>
                      {role ? <p className="text-small text-ink-2 tabular-nums">{roleDates(role)}</p> : null}
                    </div>
                    <ul className="mt-1.5 space-y-2">
                      {item.bullets.map((b, bi) => (
                        <li key={b.sourceIds.join() + bi} className="grid grid-cols-[12px_1fr] gap-x-1 sm:grid-cols-[12px_1fr_auto] sm:gap-x-3">
                          <span className="mt-[11px] size-1.5 rounded-dot bg-leaf-500" aria-hidden="true" />
                          <EditableText
                            value={b.text}
                            editable={editable}
                            edited={b.editedByUser}
                            onSave={(t) => onSave(`s${si}.i${ii}.b${bi}`, t)}
                            label={`line ${b.sourceIds[0]}`}
                            extraActions={
                              <Button variant="ghost" onClick={() => onToggleLine("remove", b.sourceIds[0])}>
                                <Minus className="size-4" aria-hidden="true" />
                                Remove line
                              </Button>
                            }
                          />
                          <span className="col-start-2 mt-1 flex flex-wrap items-start gap-1 self-start sm:col-start-3 sm:mt-0.5 sm:justify-end">
                            {b.sourceIds.map((id) => (
                              <SourceTag key={id} id={id} lines={lines} edited={b.editedByUser} />
                            ))}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </section>
          ))}

          {cv.education.length ? (
            <section className="mt-6">
              <h3 className="mb-2 text-[13px] font-bold tracking-[0.08em] text-forest-700 uppercase">Education</h3>
              {cv.education.map((ed) => (
                <div key={ed.id} className="flex flex-wrap items-baseline justify-between gap-x-4">
                  <p>
                    <span className="font-bold">{ed.degree}</span> — {ed.institution}
                  </p>
                  <p className="text-small text-ink-2 tabular-nums">{year(ed.end)}</p>
                </div>
              ))}
            </section>
          ) : null}

          <section className="mt-6">
            <h3 className="mb-2 text-[13px] font-bold tracking-[0.08em] text-forest-700 uppercase">Skills</h3>
            <p>{doc.skills.join(" · ")}</p>
          </section>
        </article>
      </Card>

      {omitted.length ? (
        <Card className="p-5">
          <h3 className="text-[15px] font-bold">Left out for this job · {omitted.length}</h3>
          <p className="mt-0.5 text-small text-ink-2">Lines from your master CV that weren&apos;t picked. Add one back in a click; it keeps its source.</p>
          <ul className="mt-3 divide-y divide-line">
            {omitted.map((id) => (
              <li key={id} className="flex items-start justify-between gap-3 py-2.5">
                <span className="text-[14px]">
                  <SourceTag id={id} lines={lines} /> <span className="ml-1">{lines[id].text}</span>
                </span>
                {editable ? (
                  <Button variant="secondary" onClick={() => onToggleLine("add", id)} aria-label={`Add ${id} back`}>
                    <Plus className="size-4" aria-hidden="true" />
                    Add
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

export function LetterPaper({
  letter,
  name,
  lines,
  editable,
  onSave,
}: {
  letter: CoverLetter;
  name: string;
  lines: CvLines;
  editable: boolean;
  onSave: Save;
}) {
  const inRange = letter.wordCount >= 250 && letter.wordCount <= 350;
  // The company facts the guard verified: from the post, and (18 Nov) from the company's own site.
  const quotes = [
    { text: letter.companyFact.quote, title: "Quoted word for word from the job post" },
    { text: letter.companyFact.siteQuote ?? "", title: "Quoted word for word from the company's own site" },
  ].filter((q) => q.text);
  const highlight = (text: string): ReactNode => {
    const q = quotes.find((x) => text.includes(x.text));
    if (!q) return text;
    const [before, ...rest] = text.split(q.text);
    return (
      <>
        {highlight(before)}
        <mark className="rounded-none bg-mint-100 px-0.5 text-ink" title={q.title}>
          {q.text}
        </mark>
        {highlight(rest.join(q.text))}
      </>
    );
  };

  return (
    <Card className="px-5 py-7 sm:px-10 sm:py-10">
      <div className="mb-6 flex flex-wrap gap-2 text-small">
        <span className={cn("rounded-none px-2.5 py-1 font-semibold", inRange ? "bg-mint-100 text-forest-900" : "bg-amber-50 text-amber-700")}>
          {letter.wordCount} words · target 250–350
        </span>
        {letter.companyFact.quote ? <span className="rounded-none bg-mint-100 px-2.5 py-1 font-semibold text-forest-900">1 company fact quoted from the post</span> : null}
        {letter.companyFact.siteQuote ? (
          <span className="rounded-none bg-mint-100 px-2.5 py-1 font-semibold text-forest-900">1 fact quoted from the company&apos;s site</span>
        ) : null}
      </div>
      <article aria-label="Cover letter" className="space-y-4 text-[15px] leading-relaxed">
        <p>{letter.greeting}</p>
        {letter.paragraphs.map((p, i) => (
          <div key={i}>
            <EditableText value={p.text} editable={editable} edited={p.editedByUser} onSave={(t) => onSave(`p${i}`, t)} label={`paragraph ${i + 1}`} render={highlight} multiline />
            <span className="mt-1.5 flex flex-wrap gap-1">
              {p.sourceIds.map((id) => (
                <SourceTag key={id} id={id} lines={lines} edited={p.editedByUser} />
              ))}
            </span>
          </div>
        ))}
        <p className="whitespace-pre-line">{letter.signoff.replace(/\n.*$/, `\n${name}`)}</p>
      </article>
    </Card>
  );
}

export function AnswerPack({
  answers,
  lines,
  editable,
  onSave,
  vendor,
}: {
  answers: AnswerItem[];
  lines: CvLines;
  editable: boolean;
  onSave: Save;
  vendor?: string;
}) {
  if (!answers.length) {
    return (
      <Card className="px-6 py-10 text-center">
        <p className="text-h2">No application questions</p>
        <p className="mx-auto mt-2 max-w-md text-ink-2">
          Answer packs are drafted from a Greenhouse job&apos;s question list.{vendor ? ` This job is on ${vendor}, so there's nothing to draft.` : ""} Use the CV and cover letter.
        </p>
      </Card>
    );
  }
  return (
    <ul className="space-y-3">
      {answers.map((a) => (
        <AnswerCard key={a.id} answer={a} lines={lines} editable={editable} onSave={onSave} />
      ))}
    </ul>
  );
}

function AnswerCard({ answer, lines, editable, onSave }: { answer: AnswerItem; lines: CvLines; editable: boolean; onSave: Save }) {
  const toast = useToast();
  const [copied, setCopied] = useState(false);
  const needed = answer.answer == null;
  return (
    <li>
      <Card className="p-5">
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-[15px] font-bold">{answer.question}</h3>
          {!needed ? (
            <Button
              variant="secondary"
              className="shrink-0 px-3"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(answer.answer!);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1500);
                } catch {
                  toast({ tone: "error", message: "Your browser blocked the clipboard. Select the text and copy it." });
                }
              }}
              aria-label={`Copy answer to: ${answer.question}`}
            >
              {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
              {copied ? "Copied" : "Copy"}
            </Button>
          ) : null}
        </div>
        {needed ? (
          <div className="mt-3 rounded-control bg-amber-50 px-3.5 py-2.5 text-[14px] text-amber-700">
            <p className="font-semibold">Your answer needed</p>
            <p>
              {answer.sensitive
                ? "WERA never drafts answers about right to work, visas, salary, or demographic and personal questions."
                : "Your master CV has no evidence for this, so WERA didn't guess."}
            </p>
            {editable ? (
              <div className="mt-2 text-ink">
                <EditableText value="" editable onSave={(t) => onSave(answer.id, t)} label={`your answer to: ${answer.question}`} render={() => <span className="text-amber-700">Write your answer</span>} />
              </div>
            ) : null}
          </div>
        ) : (
          <div className="mt-2 text-[15px] leading-relaxed whitespace-pre-line">
            <EditableText value={answer.answer!} editable={editable} onSave={(t) => onSave(answer.id, t)} label={`answer to: ${answer.question}`} multiline />
            {answer.sourceIds.length ? (
              <span className="mt-1.5 flex flex-wrap gap-1">
                {answer.sourceIds.map((id) => (
                  <SourceTag key={id} id={id} lines={lines} />
                ))}
              </span>
            ) : null}
          </div>
        )}
      </Card>
    </li>
  );
}
