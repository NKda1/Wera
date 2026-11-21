"use client";

import { Check, Download, Lock } from "lucide-react";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { upskillAnchor } from "@/lib/format";
import type { AtsReport, FormatCheck, Pack, WritingBullet, WritingReport, WritingTarget } from "@/lib/types";
import { buttonClass } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";
import { Chip, ChipList } from "@/lib/ui/Chip";
import { cn } from "@/lib/ui/cn";

export function ReviewRail({
  pack,
  appId,
  approved,
  cvVersion,
  missingSourceIds,
}: {
  pack: Pack;
  appId: string;
  approved: boolean;
  cvVersion: number;
  missingSourceIds: string[];
}) {
  const bullets = pack.cv.sections.flatMap((s) => s.items.flatMap((i) => i.bullets));
  const cited = bullets.filter((b) => b.sourceIds.length > 0).length;
  const edited = bullets.filter((b) => b.editedByUser).length + (pack.cv.summary.editedByUser ? 1 : 0) + pack.letter.paragraphs.filter((p) => p.editedByUser).length;
  const violations = [...pack.cv.guard.violations, ...pack.letter.guard.violations];

  // A column beside the document; under it (below 896px of screen) two columns once there is room,
  // rather than a single file of tablet-wide cards a long scroll from the CV.
  return (
    <div className="grid items-start gap-4 @2xl:@max-4xl:grid-cols-2">
      {pack.ats.score != null ? <AtsScoreCard ats={pack.ats} /> : null}
      <AtsCard ats={pack.ats} />

      <WritingCard appId={appId} pack={pack} />

      <FormatChecksCard docId={`${appId}.cv`} pack={pack} fallback={pack.ats.formatChecks} />

      {violations.length ? (
        <section className="rounded-card border border-amber-100 bg-amber-50 p-5 text-amber-700">
          <h2 className="text-[16px] font-bold">{violations.length === 1 ? "1 line needs a look" : `${violations.length} lines need a look`}</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-[14px]">
            {violations.map((v, i) => (
              <li key={i}>{v.detail}</li>
            ))}
          </ul>
        </section>
      ) : (
        <section className="rounded-card bg-accent-strong p-5 text-white">
          <h2 className="text-[16px] font-bold">Every line traced to your CV</h2>
          <p className="mt-1.5 text-[14px] leading-relaxed text-white/85">
            {cited} of {bullets.length} bullets cite a master-CV entry. Nothing new was invented. Click a tag to see the original line.
            {edited ? ` ${edited} edited by you.` : ""}
            {pack.cvFile?.editedByUser ? " You rewrote the CV file, and the downloads use your version." : ""}
          </p>
        </section>
      )}

      {cvVersion > pack.cv.cvVersion || missingSourceIds.length ? (
        <p className="rounded-control bg-amber-50 px-3.5 py-2.5 text-small text-amber-700">
          Built from master CV v{pack.cv.cvVersion}; you&apos;re on v{cvVersion} now.
          {missingSourceIds.length ? ` ${missingSourceIds.join(", ")} no longer exist in your CV.` : ""} Request changes to redraft it.
        </p>
      ) : null}

      {/* EXP-01: the CV and the letter, each as PDF and DOCX. Contact details are added by the route. */}
      <section aria-label="Downloads" className="flex flex-col gap-3">
        {(
          [
            ["Tailored CV", "cv"],
            ["Cover letter", "letter"],
          ] as const
        ).map(([label, kind]) => (
          <div key={kind}>
            <p className="mb-1.5 text-small font-semibold text-ink-2">{label}</p>
            <div className="grid grid-cols-2 gap-2">
              {(["pdf", "docx"] as const).map((format) => (
                <DownloadLink key={format} docId={`${appId}.${kind}`} format={format} label={label} approved={approved} />
              ))}
            </div>
          </div>
        ))}
        {!approved ? <p className="text-small text-ink-2">Approve the pack to unlock downloads.</p> : null}
      </section>
    </div>
  );
}

/**
 * ATSC-05/06, read off the PDF as it would render now — the same lint the download runs.
 *
 * Until that answers, the agent's own checks stand in, and they say so: the page count there is
 * an estimate from line lengths. These used to be the only checks shown, alongside three more the
 * agent simply asserted as passed for a PDF nobody had rendered.
 */
function FormatChecksCard({ docId, pack, fallback }: { docId: string; pack: Pack; fallback: FormatCheck[] }) {
  const [state, setState] = useState<{ checks: FormatCheck[] } | { error: string } | null>(null);

  useEffect(() => {
    let live = true;
    setState(null);
    api.getFormatChecks(docId).then(
      (r) => live && setState({ checks: r.checks }),
      (e: unknown) => live && setState({ error: e instanceof Error ? e.message : "Couldn't render the PDF." }),
    );
    return () => {
      live = false;
    };
    // An edit returns a new pack, and the PDF changes with it: a line (`cv`), or the CV file's
    // theme or text, which leave the tailored CV alone and re-measure the ATS report.
  }, [docId, pack.cv, pack.cvFile, pack.ats]);

  const checks = state && "checks" in state ? state.checks : fallback;
  return (
    <Card className="p-5">
      <h2 className="text-[16px] font-bold">Format checks</h2>
      <p className="mt-1 text-small text-ink-2">
        {state === null
          ? "Rendering the PDF to check it…"
          : "checks" in state
            ? "Read from the PDF as it will download, rendered by RenderCV."
            : `Couldn't check the rendered PDF (${state.error}). Showing estimates.`}
      </p>
      <ul className="mt-2.5 space-y-1.5 text-[14px]">
        {checks.map((c) => (
          <li key={c.id} className="flex items-start gap-2.5">
            {c.pass ? (
              <Check className="mt-0.5 size-4 shrink-0 text-forest-700" strokeWidth={2.6} aria-label="Pass" />
            ) : (
              <span className="mt-px w-4 shrink-0 text-center font-bold text-amber-500" aria-label="Needs a look">
                !
              </span>
            )}
            <span className={cn("min-w-0 break-words", c.id === "file_name" && "font-mono text-[12.5px]")}>{c.message}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** A real browser download, so the file is streamed rather than fetched into memory. */
function DownloadLink({ docId, format, label, approved }: { docId: string; format: "pdf" | "docx"; label: string; approved: boolean }) {
  const name = format.toUpperCase();
  const describe = `Download ${label} as ${name}`;
  if (!approved) {
    return (
      <button type="button" disabled className={buttonClass("secondary")} aria-label={describe}>
        <Lock className="size-3.5" aria-hidden="true" />
        {name}
      </button>
    );
  }
  return (
    <a href={`/api/documents/${encodeURIComponent(docId)}/export?format=${format}`} download className={buttonClass("secondary")} aria-label={describe}>
      <Download className="size-4" aria-hidden="true" />
      {name}
    </a>
  );
}

/** ATSC-07: the score a parser and a recruiter's search would give this CV, in five parts, each with
 *  its reason. "Best this CV could reach" separates presentation (WERA's to fix) from skills the
 *  CV doesn't have (the applicant's, on Upskills). Packs from before the score have none. */
function AtsScoreCard({ ats }: { ats: AtsReport }) {
  const score = Math.round(ats.score ?? 0);
  const best = Math.round(ats.bestScore ?? score);
  const good = score >= 70;
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-[16px] font-bold">ATS score</h2>
        <span className={cn("text-[34px] leading-none font-bold tabular-nums", good ? "text-forest-700" : "text-amber-700")}>
          {score}
          <span className="text-[16px] font-semibold text-ink-2">/100</span>
        </span>
      </div>
      <p className="mt-1 text-small text-ink-2">
        {best > score
          ? `This CV could reach ${best} on this post by showing what it already backs.`
          : "As high as this CV can score on this post — the rest is skills it doesn't show yet."}
      </p>
      <ul className="mt-3 space-y-2.5">
        {ats.parts.map((part) =>
          // 20 Nov: a part with nothing to measure has no points, and its weight went to the others.
          part.points == null ? (
            <li key={part.id}>
              <div className="flex items-baseline justify-between gap-3 text-[14px]">
                <span className="font-semibold">{part.label}</span>
                <span className="text-ink-2">not measured</span>
              </div>
              <p className="mt-0.5 text-small text-ink-2">{part.detail}</p>
            </li>
          ) : (
            <li key={part.id}>
              <div className="flex items-baseline justify-between gap-3 text-[14px]">
                <span className="font-semibold">{part.label}</span>
                <span className="tabular-nums text-ink-2">
                  {Math.round(part.points * 10) / 10} / {Math.round(part.max * 10) / 10}
                </span>
              </div>
              <div
                className="mt-1 h-1.5 overflow-hidden rounded-none bg-mint-100"
                role="progressbar"
                aria-label={part.label}
                aria-valuemin={0}
                aria-valuemax={part.max}
                aria-valuenow={part.points}
              >
                <div className="h-full rounded-none bg-accent" style={{ width: `${(100 * part.points) / Math.max(part.max, 1)}%` }} />
              </div>
              <p className="mt-0.5 text-small text-ink-2">{part.detail}</p>
            </li>
          ),
        )}
      </ul>
      {ats.parts.some((p) => p.points == null) ? (
        <p className="mt-2 text-small text-ink-2">Parts that couldn&apos;t be measured don&apos;t count; the others share their points.</p>
      ) : null}
      {ats.suggestedSkills.length ? (
        <>
          <p className="mt-4 mb-1.5 text-small text-ink-2">
            In your CV&apos;s lines but not its skills list — <a href="/cv" className="underline hover:text-forest-700">add them on your CV</a> if you&apos;d claim them
          </p>
          <ChipList>
            {ats.suggestedSkills.map((term) => (
              <Chip key={term} variant="outline">
                {term}
              </Chip>
            ))}
          </ChipList>
        </>
      ) : null}
    </Card>
  );
}

function AtsCard({ ats }: { ats: AtsReport }) {
  const matched = ats.keywords.filter((k) => k.matched);
  const gaps = ats.keywords.filter((k) => !k.matched && !k.inMasterCv);
  const backed = ats.keywords.filter((k) => !k.matched && k.inMasterCv);
  // Two numbers, because one was answering two questions and so looked wrong at both. `coverage`
  // is the literal overlap with the post and it is low for any partial match — a post naming 14
  // technologies against a CV holding 5 reads 36% however well WERA did, and beside a fit score
  // of 8 that looks like a contradiction rather than like "you can back 5 of these 14".
  // `backedCoverage` is the half WERA is accountable for, and the sentence below names the gap
  // between them so neither number has to be guessed at.
  const good = ats.backedCoverage >= 0.75;
  const value = Math.round(ats.coverage * 100);
  const coverable = Math.round(ats.backedCoverage * 100);
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-[16px] font-bold">ATS keyword coverage</h2>
        <span className={cn("text-[34px] leading-none font-bold tabular-nums", good ? "text-forest-700" : "text-amber-700")}>{value}%</span>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-none bg-mint-100" role="progressbar" aria-label="ATS keyword coverage" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}>
        <div className={cn("h-full rounded-none", good ? "bg-accent" : "bg-amber-500")} style={{ width: `${value}%` }} />
      </div>
      <p className="mt-2 text-small text-ink-2">
        {matched.length} of {ats.keywords.length} terms this post asks for are on your CV.
        {gaps.length ? ` ${coverable}% of what your CV can back is in this pack — the other ${gaps.length} aren't in your CV at all.` : " Everything your CV can back is in this pack."}
      </p>
      {ats.letterCoverage != null ? (
        <p className="mt-1.5 text-small text-ink-2">
          The cover letter carries {Math.round(ats.letterCoverage * 100)}% of the terms your CV can back. It never uses the ones it can&apos;t.
        </p>
      ) : null}

      <p className="mt-4 mb-1.5 text-small text-ink-2">Matched · {matched.length}</p>
      <ChipList>
        {matched.map((k) => (
          <Chip key={k.term} title={k.weight === "required" ? "Required" : "Nice to have"}>
            {k.term}
          </Chip>
        ))}
      </ChipList>

      {/* 20 Nov, named as resume-matcher names them: keywords the CV can back ("injectable")
          and keywords only new experience can ("non-injectable"). The first is a pack away;
          the second is Upskills' job. */}
      {backed.length ? (
        <>
          <p className="mt-4 mb-1.5 text-small text-ink-2">
            {ats.bestScore != null && Math.round(ats.bestScore) > Math.round(ats.score ?? 0)
              ? `Could reach ${Math.round(ats.bestScore)} if you add these from your CV`
              : "In your CV, not in this pack — add them from your CV"}{" "}
            · {backed.length}
          </p>
          <ChipList>
            {backed.map((k) => (
              <Chip key={k.term} variant="outline">
                {k.term}
              </Chip>
            ))}
          </ChipList>
        </>
      ) : null}

      {gaps.length ? (
        <>
          <p className="mt-4 mb-1.5 text-small text-ink-2">
            Would need new experience · {gaps.length} — add only if true, or <Link href="/upskills" className="underline hover:text-forest-700">see what to learn</Link>
          </p>
          <ChipList>
            {gaps.map((k) => (
              // UPSK-04: each gap opens the skill on Upskills — how many of your jobs ask for it.
              <Link key={k.term} href={`/upskills#${upskillAnchor(k.term)}`} className="rounded-none hover:ring-2 hover:ring-amber-500/40">
                <Chip variant="missing">{k.term}</Chip>
              </Link>
            ))}
          </ChipList>
        </>
      ) : null}
    </Card>
  );
}

const ISSUE: Record<WritingBullet["issues"][number], string> = {
  weak_verb: "Opens weakly",
  no_verb: "No action verb",
  no_number: "No number",
};

/**
 * ATSC-09 (20 Nov): how the CV reads, from the JobLeads review of 19 Nov — lines that open with
 * an action verb and say how much, a 50–100 word summary, 8–15 skills. Suggestions only, and
 * not part of the score: nothing is rewritten, and a figure is only ever asked for, never
 * offered. The "how much?" prompts are the results `cv.profile` read in each line.
 */
function WritingCard({ appId, pack }: { appId: string; pack: Pack }) {
  const [state, setState] = useState<{ report: WritingReport } | { error: string } | null>(null);
  const [all, setAll] = useState(false);

  useEffect(() => {
    let live = true;
    api.getWriting(appId).then(
      (report) => live && setState({ report }),
      (e: unknown) => live && setState({ error: e instanceof Error ? e.message : "Couldn't read the CV." }),
    );
    return () => {
      live = false;
    };
  }, [appId, pack.cv, pack.cvFile]);

  if (state === null || "error" in state) {
    return (
      <Card className="p-5">
        <h2 className="text-[16px] font-bold">Writing</h2>
        <p className="mt-1 text-small text-ink-2">{state === null ? "Reading the CV…" : `Couldn't read the CV (${state.error}).`}</p>
      </Card>
    );
  }
  const { report } = state;
  const shown = all ? report.bullets : report.bullets.slice(0, 4);
  return (
    <Card className="p-5">
      <h2 className="text-[16px] font-bold">Writing</h2>
      <p className="mt-1 text-small text-ink-2">
        Suggestions only; they don&apos;t change the ATS score. {Math.round(report.actionShare * 100)}% of {report.bulletCount} lines open with an action verb, {Math.round(report.numberShare * 100)}% give a number.
      </p>
      <ul className="mt-2.5 space-y-1.5 text-[14px]">
        <TargetRow target={report.summary} />
        <TargetRow target={report.skills} />
      </ul>
      {report.bullets.length ? (
        <>
          <p className="mt-4 mb-1.5 text-small text-ink-2">Lines to look at · {report.bullets.length}</p>
          <ul className="space-y-3">
            {shown.map((b, i) => (
              <li key={`${b.sourceId ?? "line"}-${i}`} className="border-l-2 border-amber-100 pl-3">
                <p className="text-[14px] [overflow-wrap:anywhere]">{b.text}</p>
                <p className="mt-0.5 text-small text-ink-2">
                  {b.issues.map((x) => ISSUE[x]).join(" · ")}
                  {b.sourceId ? <span className="ml-1.5 font-mono text-tag">{b.sourceId}</span> : null}
                </p>
                {b.weakPhrase && b.suggestions.length ? (
                  <p className="mt-0.5 text-small text-ink-2">
                    Instead of &ldquo;{b.weakPhrase}&rdquo;, try {b.suggestions.slice(0, -1).join(", ")}
                    {b.suggestions.length > 1 ? " or " : ""}
                    {b.suggestions[b.suggestions.length - 1]}, if it&apos;s true.
                  </p>
                ) : null}
                {b.figurePrompt ? (
                  <p className="mt-0.5 text-small text-amber-700">How much, or how many? &ldquo;{b.figurePrompt}&rdquo; — add the figure if you know it.</p>
                ) : null}
              </li>
            ))}
          </ul>
          {report.bullets.length > 4 ? (
            <button type="button" onClick={() => setAll((v) => !v)} className="mt-2 min-h-9 text-small font-semibold text-forest-700 hover:text-forest-900 pointer-coarse:min-h-11">
              {all ? "Show fewer" : `Show all ${report.bullets.length}`}
            </button>
          ) : null}
          <p className="mt-2 text-small text-ink-2">
            Change a line on <a href="/cv" className="underline hover:text-forest-700">your CV</a>, so every pack gets it.
          </p>
        </>
      ) : (
        <p className="mt-3 text-small text-ink-2">Every line opens with an action verb and gives a number.</p>
      )}
    </Card>
  );
}

function TargetRow({ target }: { target: WritingTarget }) {
  return (
    <li className="flex items-start gap-2.5">
      {target.ok ? (
        <Check className="mt-0.5 size-4 shrink-0 text-forest-700" strokeWidth={2.6} aria-label="On target" />
      ) : (
        <span className="mt-px w-4 shrink-0 text-center font-bold text-amber-500" aria-label="Off target">
          !
        </span>
      )}
      <span className="min-w-0">{target.message}</span>
    </li>
  );
}
