import type { ReactNode } from "react";
import type { DescriptionBlock, DescriptionView, JobSummary } from "../types";
import { cn } from "./cn";

// A job description laid out by the agent (`domain/description.py`, 18 Nov): the post's key facts,
// then its sections, with the company's blurb, the legal small print and how to apply folded. Every
// word is the employer's; nothing here rewrites, it only lays out. No HTML is ever injected.

const LINK = /(https?:\/\/[^\s)]+[^\s).,;:!?])/g;

/** Text with its web addresses as links, opened in a new tab. */
function Linked({ text }: { text: string }) {
  const parts = text.split(LINK);
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <a key={i} href={part} target="_blank" rel="noreferrer noopener" className="text-forest-700 underline underline-offset-2 hover:text-forest-900">
            {part}
          </a>
        ) : (
          part
        ),
      )}
    </>
  );
}

function Block({ block }: { block: DescriptionBlock }): ReactNode {
  switch (block.t) {
    case "h":
      return <p className="mt-3 mb-1 font-semibold text-ink">{block.text}</p>;
    case "ul":
    case "ol": {
      const List = block.t === "ul" ? "ul" : "ol";
      return (
        <List className={cn("my-2 space-y-1 pl-5", block.t === "ul" ? "list-disc" : "list-decimal")}>
          {(block.items ?? []).map((item, i) => (
            <li key={i}>
              <Linked text={item} />
            </li>
          ))}
        </List>
      );
    }
    default:
      return (
        <p className="my-1.5">
          <Linked text={block.text ?? ""} />
        </p>
      );
  }
}

export function JobDescription({ view }: { view: DescriptionView }) {
  return (
    <div className="text-[14px] leading-relaxed text-ink [overflow-wrap:anywhere]">
      {view.facts.length ? (
        <dl aria-label="Key facts" className="mb-4 grid gap-x-6 gap-y-1.5 border border-line bg-mint-50 px-4 py-3 text-small @2xl:grid-cols-2">
          {view.facts.map((f) => (
            <div key={f.label} className="flex min-w-0 gap-2">
              <dt className="shrink-0 font-semibold text-forest-900">{f.label}</dt>
              <dd className="min-w-0 text-ink-2">{f.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {view.sections.map((section, i) => {
        const body = section.blocks.map((b, j) => <Block key={j} block={b} />);
        if (section.fold) {
          return (
            <details key={i} className="group mt-3 border-t border-line pt-2">
              <summary className="cursor-pointer list-none text-small font-semibold text-ink-2 hover:text-ink">
                <span className="mr-1.5 inline-block transition-transform group-open:rotate-90" aria-hidden="true">
                  ›
                </span>
                {section.heading ?? "More"}
              </summary>
              <div className="mt-1 text-ink-2">{body}</div>
            </details>
          );
        }
        return (
          <section key={i} className="mt-4 first:mt-0">
            {section.heading ? <h4 className="mb-1 text-[14px] font-bold text-forest-900">{section.heading}</h4> : null}
            {body}
          </section>
        );
      })}
    </div>
  );
}

const SUMMARY_PARTS: [keyof Pick<JobSummary, "duties" | "mustHaves" | "niceToHaves" | "perks" | "watchOuts">, string][] = [
  ["duties", "What you'll do"],
  ["mustHaves", "Must have"],
  ["niceToHaves", "Nice to have"],
  ["perks", "What they offer"],
  ["watchOuts", "Worth knowing"],
];

/** `job.summary`: each item was checked against the post, and the ones it didn't support were dropped. */
export function JobSummaryView({ summary }: { summary: JobSummary }) {
  return (
    <div className="text-[14px] leading-relaxed text-ink">
      {summary.gist ? <p className="mb-3 font-semibold">{summary.gist}</p> : null}
      <div className="grid gap-4 @2xl:grid-cols-2">
        {SUMMARY_PARTS.filter(([key]) => summary[key].length).map(([key, label]) => (
          <section key={key}>
            <h4 className={cn("mb-1 text-[14px] font-bold", key === "watchOuts" ? "text-amber-700" : "text-forest-900")}>{label}</h4>
            <ul className="list-disc space-y-1 pl-5">
              {summary[key].map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      <p className="mt-3 text-small text-ink-2">
        Written by <span className="font-mono">{summary.model}</span> from the post, and checked against it: anything the post doesn&apos;t say was left out.
      </p>
    </div>
  );
}
