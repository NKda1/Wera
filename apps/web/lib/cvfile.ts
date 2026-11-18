// The CV file's `design:` block, edited as text (EXP-01, 18 Nov). The editor's theme and paper
// pickers change the YAML the person sees rather than a value beside it, so the file stays the
// one source of truth: what is in the textarea is what renders, and what gets saved.

export type PageSize = "a4" | "us-letter";

/** The line range of the top-level `design:` block: its key line to the next top-level key. */
function designBlock(lines: string[]): [number, number] | null {
  const start = lines.findIndex((l) => /^design:\s*(#.*)?$/.test(l));
  if (start === -1) return null;
  let end = start + 1;
  while (end < lines.length && (lines[end].trim() === "" || /^\s/.test(lines[end]) || lines[end].startsWith("#"))) end++;
  // Trailing blank lines belong to the gap before the next key, not to the block.
  while (end > start + 1 && lines[end - 1].trim() === "") end--;
  return [start, end];
}

/** A value inside the design block (`theme`, `size`), or null when the file doesn't set it. */
export function readDesign(yaml: string, key: "theme" | "size"): string | null {
  const lines = yaml.split("\n");
  const block = designBlock(lines);
  if (!block) return null;
  const re = new RegExp(`^\\s+${key}:\\s*['"]?([\\w-]+)['"]?\\s*(#.*)?$`);
  for (const line of lines.slice(block[0] + 1, block[1])) {
    const m = line.match(re);
    if (m) return m[1];
  }
  return null;
}

/** The file with its design block replaced by a theme's (or added, when it has none). */
export function withDesign(yaml: string, design: string): string {
  const lines = yaml.split("\n");
  const block = designBlock(lines);
  const replacement = design.replace(/\n+$/, "").split("\n");
  if (!block) return `${yaml.replace(/\n*$/, "\n")}${replacement.join("\n")}\n`;
  return [...lines.slice(0, block[0]), ...replacement, ...lines.slice(block[1])].join("\n");
}

/** The file on another paper size: the design block's `size:` changed, or added under `page:`. */
export function withPaper(yaml: string, size: PageSize): string {
  const lines = yaml.split("\n");
  const block = designBlock(lines) ?? (() => {
    lines.push("design:");
    return [lines.length - 1, lines.length] as [number, number];
  })();
  const [start, end] = block;
  for (let i = start + 1; i < end; i++) {
    const m = lines[i].match(/^(\s+)size:\s*\S+/);
    if (m) {
      lines[i] = `${m[1]}size: ${size}`;
      return lines.join("\n");
    }
  }
  const page = lines.slice(start + 1, end).findIndex((l) => /^\s+page:\s*$/.test(l));
  if (page !== -1) {
    const indent = lines[start + 1 + page].match(/^\s+/)![0];
    lines.splice(start + 2 + page, 0, `${indent}  size: ${size}`);
  } else {
    lines.splice(start + 1, 0, "  page:", `    size: ${size}`);
  }
  return lines.join("\n");
}

/** Where line `n` (1-based) starts and ends in the text, for selecting it in a textarea. */
export function lineRange(text: string, n: number): [number, number] {
  let start = 0;
  for (let i = 1; i < n; i++) {
    const next = text.indexOf("\n", start);
    if (next === -1) return [text.length, text.length];
    start = next + 1;
  }
  const end = text.indexOf("\n", start);
  return [start, end === -1 ? text.length : end];
}
