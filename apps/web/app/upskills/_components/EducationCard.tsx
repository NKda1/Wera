import type { EducationDemand, Upskill } from "@/lib/types";
import { Card } from "@/lib/ui/Card";

const LEVEL: Record<string, string> = { none: "no degree", bachelor: "a bachelor's", master: "a master's", phd: "a PhD" };
const LEVEL_SHORT: Record<string, string> = { bachelor: "Bachelor's", master: "Master's", phd: "PhD" };

/** UPSK-05 (20 Nov): the degrees the jobs ask for against the CV's education, and the programmes
 *  above its level that employers in the person's field name. Degrees are judged in the agent and
 *  never shown as skills — a BSc was once told it lacked a "Bachelor's Degree". Qualifications
 *  (CFA, ACCA…) are skills a CV can hold, so the gaps among them are listed here too. */
export function EducationCard({ education, qualifications, jobs }: { education?: EducationDemand | null; qualifications: Upskill[]; jobs: number }) {
  if (!education && !qualifications.length) return null;
  const fromPostings = education?.programmes.filter((p) => p.source === "postings") ?? [];
  const starter = education?.programmes.filter((p) => p.source === "starter") ?? [];
  return (
    <Card className="p-5" aria-labelledby="education-title">
      <h2 id="education-title" className="text-[16px] font-bold">
        Education &amp; qualifications
      </h2>
      {education ? (
        <>
          <p className="mt-1 text-small text-ink-2">
            {education.nearMisses
              ? `${education.jobsAsking - education.nearMisses} of ${jobs} jobs on your list and ${education.nearMisses} you nearly matched name a degree.`
              : `${education.jobsAsking} of ${jobs} jobs name a degree.`}{" "}
            Your CV shows {LEVEL[education.cvLevel] ?? education.cvLevel}, which meets{" "}
            {education.met} of them
            {education.levelGaps ? ` — ${education.levelGaps} ask for a higher level` : ""}
            {education.subjectGaps ? `${education.levelGaps ? "," : " —"} ${education.subjectGaps} for a different subject` : ""}.
          </p>
          {education.levels.length ? (
            <ul className="mt-3 flex flex-wrap gap-2 text-small">
              {education.levels.map((l) => (
                <li key={l.level} className={l.met ? "bg-mint-100 px-2.5 py-1 text-forest-900" : "bg-amber-50 px-2.5 py-1 text-ink"}>
                  {LEVEL_SHORT[l.level]} · {l.jobs} job{l.jobs === 1 ? "" : "s"}
                  {l.met ? " · you have this" : ""}
                </li>
              ))}
            </ul>
          ) : null}
          {education.programmes.length ? (
            <div className="mt-4">
              <h3 className="text-[14px] font-semibold">Programmes employers in your field ask for</h3>
              <p className="text-small text-ink-2">
                {fromPostings.length
                  ? "Named in the postings WERA read for you, most named first."
                  : "Too few postings have named one yet, so this is a starting list for your field."}
                {fromPostings.length && starter.length ? " Below them, a starting list for your field while more postings come in." : ""}
              </p>
              <ul className="mt-2 grid gap-x-6 gap-y-1.5 md:grid-cols-2">
                {education.programmes.map((p) => (
                  <li key={p.name} className="text-[14px]">
                    <span className="font-semibold">{p.name}</span>
                    <span className="text-ink-2">{p.source === "postings" ? ` · named by ${p.mentions} posting${p.mentions === 1 ? "" : "s"}` : " · starting list"}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {education.gapExamples.length ? (
            <p className="mt-3 text-small text-ink-2">
              For example: {education.gapExamples.map((e) => `${e.title} at ${e.company}`).join("; ")}.
            </p>
          ) : null}
        </>
      ) : null}
      {qualifications.length ? (
        <div className={education ? "mt-4" : "mt-2"}>
          <h3 className="text-[14px] font-semibold">Qualifications employers ask for</h3>
          <ul className="mt-2 flex flex-wrap gap-2 text-small">
            {qualifications.map((q) => (
              <li key={q.term} className="bg-mint-100 px-2.5 py-1 text-forest-900">
                {q.term} · {q.jobs} job{q.jobs === 1 ? "" : "s"}
                {q.nearMisses ? ` · ${q.nearMisses} nearly matched` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}
