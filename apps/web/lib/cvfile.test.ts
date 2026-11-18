import { describe, expect, it } from "vitest";
import { lineRange, readDesign, withDesign, withPaper } from "./cvfile";

const FILE = `# A RenderCV file
cv:
  name: Jordan Ellery
  sections:
    Summary:
      - Built things.
design:
  theme: engineeringresumes
  page:
    size: a4
    show_top_note: false
  header:
    connections:
      show_icons: false

locale:
  language: english
`;

const SB2NOV = `design:
  theme: sb2nov
  page:
    size: a4
    show_top_note: false
  typography:
    alignment: justified-with-no-hyphenation
`;

describe("the CV file's design block", () => {
  it("reads the theme and paper", () => {
    expect(readDesign(FILE, "theme")).toBe("engineeringresumes");
    expect(readDesign(FILE, "size")).toBe("a4");
    expect(readDesign("cv:\n  name: x\n", "theme")).toBeNull();
  });

  it("swaps in a theme's design and leaves the rest alone", () => {
    const next = withDesign(FILE, SB2NOV);
    expect(readDesign(next, "theme")).toBe("sb2nov");
    expect(next).toContain("alignment: justified-with-no-hyphenation");
    expect(next).not.toContain("show_icons");
    expect(next.startsWith(FILE.slice(0, FILE.indexOf("design:")))).toBe(true);
    expect(next.endsWith("\nlocale:\n  language: english\n")).toBe(true);
  });

  it("adds a design to a file that has none", () => {
    expect(readDesign(withDesign("cv:\n  name: x\n", SB2NOV), "theme")).toBe("sb2nov");
  });

  it("changes the paper where it is set, or adds it", () => {
    expect(readDesign(withPaper(FILE, "us-letter"), "size")).toBe("us-letter");
    expect(withPaper(FILE, "us-letter").match(/size:/g)).toHaveLength(1);
    const noPage = "cv:\n  name: x\ndesign:\n  theme: classic\n";
    expect(readDesign(withPaper(noPage, "us-letter"), "size")).toBe("us-letter");
    expect(readDesign(withPaper("cv:\n  name: x\n", "a4"), "size")).toBe("a4");
  });

  it("never mistakes a size inside the cv for the paper", () => {
    const tricky = FILE.replace("  name: Jordan Ellery\n", "  name: Jordan Ellery\n  size: huge\n");
    expect(readDesign(withPaper(tricky, "us-letter"), "size")).toBe("us-letter");
    expect(withPaper(tricky, "us-letter")).toContain("  size: huge\n");
  });

  it("finds a line to point at", () => {
    const [start, end] = lineRange(FILE, 3);
    expect(FILE.slice(start, end)).toBe("  name: Jordan Ellery");
  });
});
