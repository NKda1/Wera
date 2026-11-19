# Job filters: one line of dropdowns instead of a wall of chips (18 Nov 2026)

## Why
The Jobs screen's filters are "really hard to use and mess the user experience" (user, 18 Nov,
with a screenshot). Six groups — Where, Level, Working, Found by, Found in, Fit — render as one
wrapping strip of chips, each option a chip with a count, groups split only by a hairline. The
group names exist in code but are never shown, so it reads as five unlabelled rows taking a third
of the screen before the first job, and long labels are clipped at the right edge ("Bedf…",
"Employer feed 18 Nov, 2…").

## Goals
- The same filtering power in one line, so the first job is near the top.
- Every group named; every option readable in full, with its count.
- Active filters visible at a glance and removable in one click.

## Non-goals
- No change to what the filters do: within a group options are "either", across groups "and";
  groups with fewer than two options stay hidden; a stale selection still quietly stops applying.
- No change to the sort switch (Best match · Newest · Just found · By urgency) or All / New.
- No server change; filters stay client-side and unsaved between visits.

## Anti-goals
- A filter hidden behind more than one click to change.
- Anything clipped or scrolled out of sight without a cue.

## Approaches
- **A. A dropdown per group (picked by the user).** One row: All / New, then `Where ▾ Level ▾
  Working ▾ Found by ▾ Found in ▾ Fit ▾`, then sort. Each opens a checkbox list with counts.
  Active filters as removable chips underneath, with "Clear all". Trade-off: a group's options
  are seen only when opened.
- B. One "Filters" button opening a labelled panel of every group. Tidy, but every change is an
  extra click.
- C. Keep the chips, label each row, collapse behind "More filters". Least change; still long
  once expanded.

## Design (A)
```
[All 55] [New 51]  Where · London ▾  Level ▾  Working ▾  Found by ▾  Found in ▾  Fit ▾   Best match | Newest | Just found | By urgency
Where: London ×   Clear all
```
- **Buttons:** the group's name; with selections, the name plus the first choice and "+N"
  ("Where · London +1"), in the accent style. Groups with fewer than two options don't appear.
- **Dropdown:** the group's options, biggest first, each a checkbox with its full label and count.
  A search box at the top when a group has more than 8 options (places, searches). "Clear" at the
  bottom when anything in it is chosen. Toggling applies at once; the list stays open until a
  click outside or Escape. Keyboard: Tab to the button, Enter/Space opens, arrows move, Space
  toggles, Escape closes and returns focus to the button.
- **Active chips:** one per choice, "Group: Option ×", then "Clear all" — only when any are set.
- **Labels:** levels capitalised ("Graduate"), working modes too ("Remote", "On-site").
- **Narrow screens:** the button row scrolls sideways with the existing edge fade; a dropdown
  opens full-width under the row; sort drops below as it does today.
- The "+N more" mechanism (`FACETS_SHOWN`, `openGroups`) goes: a dropdown has room for everything.

## Builder's calls
- Component: a new `FilterMenu` in `app/jobs/_components/`, built on the existing button, chip
  and focus styles (no new dependency).
- Tests: update the two e2e journeys that click chips (`J2` fit filter, `layout.spec` filter
  group) and add one for open → tick → chip → clear.
