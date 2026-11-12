"use client";

import { Check, Download, Lock } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AtsReport, FormatCheck, Pack } from "@/lib/types";
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

  return (
    <div className="flex flex-col gap-4">
      <AtsCard ats={pack.ats} />

      <FormatChecksCard docId={`${appId}.cv`} cv={pack.cv} fallback={pack.ats.formatChecks} />

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
        <section className="rounded-card bg-forest-900 p-5 text-white">
          <h2 className="text-[16px] font-bold">Every line traced to your CV</h2>
          <p className="mt-1.5 text-[14px] leading-relaxed text-white/85">
            {cited} of {bullets.length} bullets cite a master-CV entry. Nothing new was invented. Click a tag to see the original line.
            {edited ? ` ${edited} edited by you.` : ""}
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
function FormatChecksCard({ docId, cv, fallback }: { docId: string; cv: Pack["cv"]; fallback: FormatCheck[] }) {
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
    // `cv` is the dependency that matters: an edit returns a new pack, and the PDF changes with it.
  }, [docId, cv]);

  const checks = state && "checks" in state ? state.checks : fallback;
  return (
    <Card className="p-5">
      <h2 className="text-[16px] font-bold">Format checks</h2>
      <p className="mt-1 text-small text-ink-2">
        {state === null
          ? "Rendering the PDF to check it…"
          : "checks" in state
            ? "Read from the PDF as it will download."
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
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-mint-100" role="progressbar" aria-label="ATS keyword coverage" aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}>
        <div className={cn("h-full rounded-full", good ? "bg-forest-700" : "bg-amber-500")} style={{ width: `${value}%` }} />
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

      {backed.length ? (
        <>
          <p className="mt-4 mb-1.5 text-small text-ink-2">In your CV, not in this pack · {backed.length}</p>
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
          <p className="mt-4 mb-1.5 text-small text-ink-2">Missing · {gaps.length} — add only if true</p>
          <ChipList>
            {gaps.map((k) => (
              <Chip key={k.term} variant="missing">
                {k.term}
              </Chip>
            ))}
          </ChipList>
        </>
      ) : null}
    </Card>
  );
}
