import "server-only";

// ATSC-05 wants embedded fonts, so the exports ship their own instead of relying on the
// PDF standard 14. Source Sans 3 is under the SIL Open Font License; see fonts/LICENSE.md.

import { fileURLToPath } from "node:url";
import { Font } from "@react-pdf/renderer";

export const FONT_FAMILY = "Source Sans 3";

// Spelled out, one literal per file: the bundler reads these `new URL` calls statically and
// emits each font next to the route. A template string collapses them all onto one asset.
const REGULAR = fileURLToPath(new URL("./fonts/SourceSans3-Regular.ttf", import.meta.url));
const BOLD = fileURLToPath(new URL("./fonts/SourceSans3-Bold.ttf", import.meta.url));

let registered = false;

/** Idempotent: @react-pdf keeps one global font store per process. */
export function registerFonts(): void {
  if (registered) return;
  Font.register({
    family: FONT_FAMILY,
    fonts: [
      { src: REGULAR, fontWeight: 400 },
      { src: BOLD, fontWeight: 700 },
    ],
  });
  // Our own line breaking: the default hyphenation callback splits words, which ATS
  // parsers then read as two tokens.
  Font.registerHyphenationCallback((word) => [word]);
  registered = true;
}
