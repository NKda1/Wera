"use client";

import { ExternalLink } from "lucide-react";
import type { ApplicationWithJob, BriefItem, CompanyBrief } from "@/lib/types";
import { Button } from "@/lib/ui/Button";
import { Card } from "@/lib/ui/Card";

const TOPICS: [BriefItem["topic"], string][] = [
  ["what_they_do", "What they do"],
  ["who_they_serve", "Who they serve"],
  ["size_or_stage", "Size and stage"],
  ["values", "What they say they value"],
  ["why_the_role", "Why they're hiring"],
];

const host = (url: string) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
};

/**
 * 18 Nov: what the post and the company's own About page say about the employer. Every item shows
 * the words it rests on and where they are; WERA dropped any item those words didn't support. The
 * letter can be rewritten with it, and quotes the site only word for word.
 */
export function CompanyPanel({
  app,
  editable,
  busy,
  onLearn,
  onRewrite,
}: {
  app: ApplicationWithJob;
  editable: boolean;
  busy?: "learn" | "rewrite";
  onLearn: () => void;
  onRewrite: () => void;
}) {
  const brief = app.pack?.company;
  const company = app.job.company;
  if (!brief) {
    return (
      <Card className="p-6">
        <h2 className="text-h2">Learn about {company}</h2>
        <p className="mt-2 max-w-[60ch] text-ink-2">
          WERA reads the job post and, if the site&apos;s robots.txt allows it, one page from {company}&apos;s own website — never LinkedIn,
          and never an agency&apos;s site. Every fact keeps the words it came from, and anything those words don&apos;t say is left out. Your
          letter can then say why this company, with a fact you can check.
        </p>
        <Button className="mt-4" busy={busy === "learn"} onClick={onLearn}>
          Learn about {company}
        </Button>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <Card className="p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h2 className="text-h2">What WERA learned about {brief.company}</h2>
          <Button variant="secondary" busy={busy === "learn"} onClick={onLearn}>
            Read again
          </Button>
        </div>
        <p className="mt-1 text-small text-ink-2">
          {brief.site ? (
            <>
              From the job post and{" "}
              <a href={brief.site.url} target="_blank" rel="noreferrer noopener" className="font-semibold text-forest-700 underline underline-offset-2">
                {host(brief.site.url)}
                <ExternalLink className="ml-0.5 inline size-3" aria-hidden="true" />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
              , checked against both.
            </>
          ) : (
            "From the job post alone."
          )}
        </p>
        {brief.siteNote ? <p className="mt-3 rounded-control bg-amber-50 px-3.5 py-2.5 text-small text-amber-700">{brief.siteNote}</p> : null}
        {brief.items.length ? (
          <div className="mt-4 space-y-4">
            {TOPICS.filter(([t]) => brief.items.some((i) => i.topic === t)).map(([topic, label]) => (
              <section key={topic}>
                <h3 className="text-[14px] font-bold text-forest-900">{label}</h3>
                <ul className="mt-1 space-y-2.5">
                  {brief.items
                    .filter((i) => i.topic === topic)
                    .map((item, i) => (
                      <BriefLine key={i} item={item} brief={brief} />
                    ))}
                </ul>
              </section>
            ))}
          </div>
        ) : (
          <p className="mt-4 text-ink-2">Nothing in the post{brief.site ? " or on the page" : ""} said enough about the company to quote.</p>
        )}
      </Card>
      {editable ? (
        <Card className="p-5">
          <h3 className="text-[15px] font-bold">Use it in the letter</h3>
          <p className="mt-1 text-small text-ink-2">
            {app.pack?.letter.companyFact.siteQuote
              ? "Your letter already quotes their site."
              : "WERA redrafts the letter alone, with these facts available to it. It may quote one from their site, word for word, beside a line from your CV that shows why it suits you. The CV is kept as it is."}
          </p>
          <Button className="mt-3" variant="secondary" busy={busy === "rewrite"} onClick={onRewrite}>
            Rewrite the letter with this brief
          </Button>
        </Card>
      ) : null}
    </div>
  );
}

function BriefLine({ item, brief }: { item: BriefItem; brief: CompanyBrief }) {
  return (
    <li className="text-[14px] leading-relaxed">
      <p>{item.text}</p>
      <p className="mt-0.5 border-l-2 border-mint-200 pl-2.5 text-small text-ink-2">
        “{item.quote}” —{" "}
        {item.source === "site" && brief.site ? (
          <a href={brief.site.url} target="_blank" rel="noreferrer noopener" className="underline underline-offset-2 hover:text-forest-700">
            {host(brief.site.url)}
          </a>
        ) : (
          "the job post"
        )}
      </p>
    </li>
  );
}
