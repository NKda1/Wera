import type { ReactNode } from "react";

/** Minimal, text-only Markdown for job descriptions: headings, bullet lists, paragraphs. No HTML is ever injected. */
export function Markdown({ source }: { source: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(
        <ul key={blocks.length} className="my-2 list-disc space-y-1 pl-5">
          {list.map((li, i) => (
            <li key={i}>{li}</li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  for (const raw of source.split("\n")) {
    const line = raw.trim();
    if (line.startsWith("- ") || line.startsWith("* ")) {
      list.push(line.slice(2));
      continue;
    }
    flush();
    if (!line) continue;
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (heading) {
      blocks.push(
        <h4 key={blocks.length} className="mt-4 mb-1 text-[14px] font-bold text-forest-900 first:mt-0">
          {heading[1]}
        </h4>,
      );
    } else {
      blocks.push(
        <p key={blocks.length} className="my-1.5">
          {line}
        </p>,
      );
    }
  }
  flush();
  return <div className="text-[14px] leading-relaxed text-ink">{blocks}</div>;
}
