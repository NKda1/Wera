# Job filters as dropdowns — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Jobs screen's wrapping strip of filter chips with one row of per-group dropdowns plus removable active-filter chips.

**Architecture:** Pure helpers (labels, button text, search) in a small module with unit tests; one `FilterMenu` client component (button + checkbox popover); `JobsScreen` swaps its chip strip for a row of `FilterMenu`s and an active-chip line. Filter semantics and state (`filters: string[]` of facet ids) are unchanged.

**Tech Stack:** Next.js 16 / React 19, Tailwind (WERA tokens), Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-18-job-filters-design.md`

## Global Constraints
- Semantics unchanged: "either" within a group, "and" across groups; a group with fewer than two options is not shown; a stale selection quietly stops applying.
- Sort switch and All / New unchanged.
- No new dependency; reuse the existing button/chip/focus styles (`FilterChip`, `buttonClass`, `cn`).
- Search box only when a group has **more than 8** options.
- Button text: group name; with choices, `"{Group} · {first label}"` plus `" +{n-1}"` when more than one.
- Active chip text: `"{Group}: {Option}"` with a remove control; then "Clear all".
- Labels: levels and working modes capitalised; `onsite` reads `On-site`.
- Commits are the user's (repo rule): the commit step in each task is "leave staged changes for the user", never `git commit`.

## Review Focus
- A group with many options (20+ cities): list scrolls inside the popover, never past the viewport → test in Task 2 (max height + overflow class present; search filters).
- Click outside / Escape / Tab away closes and returns focus to the button → test in Task 3 e2e.
- A selected option that drops off the shortlist (job decided) leaves no ghost chip → Task 3 renders chips from facets present, not raw ids.
- Phone width (390px): row scrolls, popover fits the screen width → Task 3 phone e2e assertion.
- Search with no matches shows "No matches", not an empty box → Task 2 test.

---

### Task 1: Labels and button text

**Files:**
- Create: `apps/web/app/jobs/_components/filters.ts`
- Test: `apps/web/app/jobs/_components/filters.test.ts`
- Modify: `apps/web/app/jobs/_components/JobsScreen.tsx` (`facetsFor`: level and work labels via the new helpers)

**Interfaces:**
- Produces:
  - `levelLabel(level: string): string` — `"graduate"` → `"Graduate"`, `"mid_senior"` → `"Mid senior"`
  - `workLabel(mode: string): string` — `remote` → `Remote`, `hybrid` → `Hybrid`, `onsite` → `On-site`
  - `buttonText(group: string, chosen: string[]): string`
  - `SEARCH_FROM = 8` and `matches(label: string, query: string): boolean` (case- and accent-insensitive substring)

- [ ] **Step 1: Write the failing tests**
```ts
expect(levelLabel("graduate")).toBe("Graduate");
expect(workLabel("onsite")).toBe("On-site");
expect(buttonText("Where", [])).toBe("Where");
expect(buttonText("Where", ["London"])).toBe("Where · London");
expect(buttonText("Where", ["London", "Toronto", "Zürich"])).toBe("Where · London +2");
expect(matches("Zürich", "zur")).toBe(true);
expect(matches("Search 18 Nov, 23:27", "23:2")).toBe(true);
expect(matches("London", "paris")).toBe(false);
```
- [ ] **Step 2:** `pnpm vitest run app/jobs/_components/filters.test.ts` → FAIL (module missing).
- [ ] **Step 3:** Implement the four exports; use them in `facetsFor` for level and work labels.
- [ ] **Step 4:** Same command → PASS; `pnpm typecheck` clean.
- [ ] **Step 5:** Leave changes staged for the user.

### Task 2: `FilterMenu` component

**Files:**
- Create: `apps/web/app/jobs/_components/FilterMenu.tsx`

**Interfaces:**
- Consumes: `buttonText`, `matches`, `SEARCH_FROM` from Task 1.
- Produces: `FilterMenu({ group, options, selected, onToggle, onClear }: { group: string; options: { id: string; label: string; count: number }[]; selected: string[]; onToggle: (id: string) => void; onClear: () => void })`

Behaviour (the spec's): button `aria-haspopup="true"`, `aria-expanded`, accent style when `selected.length`; popover `role="dialog"` `aria-label="{group} filters"` holding a list of native checkboxes (label + count), biggest first as given; search input (label "Search {group}") when `options.length > SEARCH_FROM`; "No matches" when the query matches nothing; "Clear" when anything is selected; `max-h-[min(60vh,420px)] overflow-y-auto`; closes on outside pointerdown, Escape (focus back to button); ArrowDown/ArrowUp move focus between the search box and the checkboxes, Space toggles (native); positioned under the button, `w-[min(320px,calc(100vw-32px))]`.

- [ ] **Step 1:** Covered by Task 3's e2e (component has no unit-test harness in this repo: Vitest runs node, no DOM). Typecheck only here.
- [ ] **Step 2:** `pnpm typecheck` clean.
- [ ] **Step 3:** Leave staged.

### Task 3: Jobs screen row, active chips, e2e

**Files:**
- Modify: `apps/web/app/jobs/_components/JobsScreen.tsx:476-541` (toolbar), remove `FACETS_SHOWN`, `openGroups`, the "+N" button
- Modify: `apps/web/e2e/journeys.spec.ts:35-37`, `apps/web/e2e/layout.spec.ts:42`, `apps/web/e2e/phone.spec.ts` (one assertion)

**Interfaces:**
- Consumes: `FilterMenu` (Task 2); existing `facets`, `filters`, `setFilters`, `GROUP_LABELS`, `FilterChip`, `SortSwitch`.

Layout: row 1 = All/New chips, one `FilterMenu` per group with ≥2 options (in `GROUP_LABELS` order), sort switch right (wraps below when narrow, as now); the menu row keeps the existing sideways scroll + edge fade on phones (`role="group" aria-label="Filter jobs"` stays). Row 2 (only when a facet in `facets` is selected): one chip per selected facet, `"{GROUP_LABELS[group]}: {label}"`, with a "Remove {label}" button, then "Clear all".

- [ ] **Step 1: Update/add e2e**
  - J2: `await page.getByRole("button", { name: /^Fit/ }).click(); await page.getByRole("checkbox", { name: /8\+/ }).check(); await page.keyboard.press("Escape");` → `cards` count 2; chip `Fit: 8+` visible; `getByRole("button", { name: "Clear all" }).click()` → count 12.
  - New journey "Filters: a dropdown per group": open Where, tick London, chip "Where: London" visible, button reads `Where · London`; click outside closes the dialog and focus is on the button; Escape also closes; "Remove London" removes it.
  - layout.spec: the filter group sits on one row at 1440px (all `FilterMenu` buttons share a `y`).
  - phone.spec (390px): open Where; the dialog's box is within the viewport width.
- [ ] **Step 2:** `pnpm test:e2e --grep "J2|Filters|layout|phone"` → new assertions FAIL against the chip strip.
- [ ] **Step 3:** Implement the toolbar as above.
- [ ] **Step 4:** `pnpm typecheck && pnpm test && pnpm test:e2e` → all pass (needs the user's `pnpm dev` stopped; restart it after).
- [ ] **Step 5:** Leave staged for the user.
