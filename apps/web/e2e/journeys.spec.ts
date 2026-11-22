import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";

// J2 and J3 in demo mode, in order, against one fresh server.
test.describe.configure({ mode: "serial" });

const cards = (page: Page) => page.getByRole("region", { name: "Shortlisted jobs" }).getByRole("heading", { level: 3 });

test.beforeEach(async ({ page }) => {
  // T-04: nothing in the browser ever talks to LinkedIn or a provider directly.
  page.on("request", (req) => {
    const host = new URL(req.url()).hostname;
    expect(host.endsWith("linkedin.com"), `request to ${req.url()}`).toBe(false);
    expect(["localhost", "fonts.gstatic.com", "fonts.googleapis.com"].includes(host), `request to ${host}`).toBe(true);
  });
});

test("J2: search, filter and approve from the shortlist", async ({ page }) => {
  await page.goto("/jobs");
  await expect(page.getByRole("heading", { name: "Today's shortlist" })).toBeVisible();
  // LIFE-03: two seeded jobs were first seen over a week ago and never confirmed open, so they wait
  // under "Show possibly closed" rather than on the shortlist.
  await expect(cards(page)).toHaveCount(10);
  await expect(page.getByRole("link", { name: "Show possibly closed" })).toBeVisible();
  await expect(page.getByText("8.6", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Search now" }).click();
  const dialog = page.getByRole("dialog", { name: "Search now" });
  await expect(dialog.getByText(/Google searches to start/).first()).toBeVisible();
  await dialog.getByRole("button", { name: "Run search" }).click();
  await expect(page.getByRole("heading", { name: "Running your search…" })).toBeVisible();
  await expect(page.getByText("Search finished: 2 new jobs added.")).toBeVisible();
  await expect(cards(page)).toHaveCount(12);

  const filters = page.getByRole("group", { name: "Filter jobs" });
  await filters.getByRole("button", { name: /^Fit/ }).click();
  await page.getByRole("checkbox", { name: /8\+/ }).check();
  await page.keyboard.press("Escape");
  await expect(cards(page)).toHaveCount(2);
  await expect(page.getByText("Fit: 8+", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear all" }).click();
  await expect(cards(page)).toHaveCount(12);

  const fernhill = page.getByRole("region", { name: "ML Engineer, Graduate" });
  await fernhill.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Approved ML Engineer, Graduate. Drafting your pack…")).toBeVisible();
  await expect(cards(page)).toHaveCount(11);

  const harbour = page.getByRole("region", { name: "Graduate Data Engineer" });
  await harbour.getByRole("button", { name: "Snooze" }).click();
  await page.getByRole("menuitem", { name: "3 days" }).click();
  await expect(cards(page)).toHaveCount(10);
});

test("Filters: a dropdown per group", async ({ page }) => {
  await page.goto("/jobs");
  // The profile card above has a "Where" button of its own.
  const where = page.getByRole("group", { name: "Filter jobs" }).getByRole("button", { name: /^Where/ });
  await where.click();
  const dialog = page.getByRole("dialog", { name: "Where filters" });
  await dialog.getByRole("checkbox", { name: /^London/ }).check();
  await expect(page.getByText("Where: London")).toBeVisible();
  await expect(where).toHaveText(/^Where · London/);

  // Clear empties the group but keeps focus in the popover, so the keyboard isn't thrown to <body>.
  await dialog.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByText("Where: London")).toHaveCount(0);
  await expect(dialog.locator(":focus")).toHaveCount(1);
  await page.keyboard.press("ArrowDown");
  await expect(dialog.getByRole("checkbox").and(dialog.locator(":focus"))).toHaveCount(1);
  await dialog.getByRole("checkbox", { name: /^London/ }).check();

  // An outside click closes it and it closes.
  await page.getByRole("heading", { name: "Today's shortlist" }).click();
  await expect(dialog).toHaveCount(0);

  await where.click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(where).toBeFocused();

  await page.getByRole("button", { name: "Remove Where: London" }).click();
  await expect(page.getByText("Where: London")).toHaveCount(0);
  await expect(where).toHaveText("Where");
});

test("Urgency grouping files jobs by how old the posting is, and hedges where the date is a guess", async ({ page }) => {
  await page.goto("/jobs");
  const region = page.getByRole("region", { name: "Shortlisted jobs" });
  // The route streams behind a skeleton (`app/jobs/loading.tsx`), so wait for the list itself.
  await expect(region.getByRole("heading", { level: 3 }).first()).toBeVisible();
  const cardCount = await region.getByRole("heading", { level: 3 }).count();

  // Default: one flat list, no group headers.
  await expect(region.getByRole("heading", { level: 2 })).toHaveCount(0);

  await page.getByRole("radiogroup", { name: "Sort" }).getByRole("radio", { name: "By urgency" }).click();
  const groups = region.getByRole("heading", { level: 2 });
  await expect(groups.first()).toHaveText("Today and yesterday");
  // Grouping is a view, not a filter: every job is still on the page, just under a header.
  await expect(region.getByRole("heading", { level: 3 })).toHaveCount(cardCount);
  // An empty bucket gets no header — "Over two weeks old (0)" is noise, not information.
  for (let i = 0; i < (await groups.count()); i++) {
    await expect(groups.nth(i)).not.toHaveText("No date given");
  }

  // Only the employer's own feed gives a real timestamp; a board or alert says "3 days ago",
  // and the card has to say so rather than quietly rounding it into a claim.
  await expect(page.getByText(/about \d+ days ago/).first()).toBeVisible();
});

test("J3: review the drafted pack, approve it and track it", async ({ page }) => {
  await page.goto("/review");
  const row = page.getByRole("link", { name: "ML Engineer, Graduate" });
  await expect(row).toBeVisible();
  await row.click();

  await expect(page.getByText("Gate 2 · needs your approval")).toBeVisible();
  // Coverage moved 86% → 85% on 14 Nov with the synonym groups and the post's spelling on the skills line.
  await expect(page.getByText("85%")).toBeVisible();
  await expect(page.getByRole("heading", { name: "ATS score" })).toBeVisible(); // ATSC-07
  await expect(page.getByText("Docker", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Tailored CV as PDF" })).toBeDisabled();

  await page.getByRole("button", { name: "Source exp-idsu-01: show the original line" }).first().click();
  await expect(page.getByRole("dialog", { name: "Original line exp-idsu-01" })).toContainText("Master CV · AI Intern");
  await page.keyboard.press("Escape");

  await page.getByRole("tab", { name: "Cover letter" }).click();
  await expect(page.getByText("Dear Hiring Team,")).toBeVisible();
  await page.getByRole("tab", { name: "Answer pack · 6" }).click();
  await expect(page.getByText("Your answer needed")).toHaveCount(3);

  await page.getByRole("tab", { name: "Tailored CV" }).click();
  await page.getByRole("button", { name: "Edit summary" }).click();
  await page.getByRole("textbox", { name: "summary" }).fill("CS graduate (2026) building production AI tooling.");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("edited by you", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Approve pack" }).click();
  await expect(page.getByText("Approved · ready to send")).toBeVisible();

  // EXP-01: all four files, as real downloads with ATS-safe names (ATSC-05).
  for (const [label, expected] of [
    ["Tailored CV as PDF", "Jordan-Ellery-CV-Fernhill-AI.pdf"],
    ["Tailored CV as DOCX", "Jordan-Ellery-CV-Fernhill-AI.docx"],
    ["Cover letter as PDF", "Jordan-Ellery-Letter-Fernhill-AI.pdf"],
    ["Cover letter as DOCX", "Jordan-Ellery-Letter-Fernhill-AI.docx"],
  ] as const) {
    const link = page.getByRole("link", { name: `Download ${label}` });
    const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
    expect(download.suggestedFilename()).toBe(expected);
    const file = await download.path();
    const bytes = await readFile(file);
    expect(bytes.byteLength).toBeGreaterThan(5_000);
    // A real PDF or a real zip (.docx), not an error page.
    expect(bytes.subarray(0, 4).toString("latin1")).toBe(expected.endsWith(".pdf") ? "%PDF" : "PK\x03\x04");
    if (expected.endsWith(".pdf")) {
      // ATSC-05/06 on the file itself — for the CV, the agent's RenderCV render (EXP-01, 18 Nov).
      const res = await page.request.get((await link.getAttribute("href"))!);
      expect(res.headers()["x-wera-format-lint"]).toBe("pass");
    }
  }

  await page.getByRole("button", { name: "I've submitted it" }).click();
  await expect(page.getByText(/Submitted \d/)).toBeVisible();

  await page.goto("/tracker");
  await expect(page.getByRole("region", { name: "Submitted" })).toContainText("ML Engineer, Graduate");
});

test("The CV file: a theme, a rewrite that locks line editing, and back (EXP-01)", async ({ page }) => {
  await page.goto("/review");
  await page.getByRole("link", { name: "Graduate AI Engineer" }).click();
  await expect(page.getByText("Gate 2 · needs your approval")).toBeVisible();
  await page.getByRole("button", { name: "CV file and design" }).click();

  const file = page.getByRole("textbox", { name: "CV file (RenderCV YAML)" });
  await expect(file).toHaveValue(/theme: engineeringresumes/);
  // No contact details in the file: they are added when the PDF is made (EXP-02).
  expect(await file.inputValue()).not.toContain("jordan.ellery@example.com");
  await expect(page.getByRole("img", { name: "Page 1 of the CV" })).toBeVisible();
  await expect(page.getByText("1 page, as the PDF will download.")).toBeVisible();

  // A theme is a design, and a design alone keeps the CV WERA's.
  await page.getByLabel("CV theme").selectOption("harvard");
  await expect(file).toHaveValue(/theme: harvard/);
  await expect(page.getByText("1 page, as the PDF will download.")).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved. The PDF uses this design.")).toBeVisible();

  // What the file can't hold says so, at its line, and can't be saved.
  const saved = await file.inputValue();
  await file.fill(saved.replace("  name: Jordan Ellery\n", "  name: Jordan Ellery\n  email: me@example.com\n"));
  await expect(page.getByText("Your email address is added when the PDF is made, from your contact details. Remove it here.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Undo changes" }).click();

  // A rewrite is the person's CV: the downloads use it and line editing waits.
  await file.fill(saved.replace("  sections:\n", "  sections:\n    Awards:\n      - Rewritten by me in the file editor\n"));
  await expect(page.getByText("1 page, as the PDF will download.")).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved. The PDF and Word files use your version of the CV.")).toBeVisible();
  await page.getByRole("button", { name: "Lines and sources" }).click();
  await expect(page.getByText(/You rewrote this CV in its file/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit summary" })).toHaveCount(0);

  await page.getByRole("button", { name: "Open the CV file" }).click();
  await page.getByRole("button", { name: "Discard my edits" }).click();
  await page.getByRole("dialog", { name: "Discard your edits to the CV file?" }).getByRole("button", { name: "Discard" }).click();
  await expect(page.getByText("Back to WERA's version. Your design stays.")).toBeVisible();
  await expect(file).not.toHaveValue(/Rewritten by me/);
  await expect(page.getByLabel("CV theme")).toHaveValue("harvard");
  await page.getByRole("button", { name: "Lines and sources" }).click();
  await expect(page.getByRole("button", { name: "Edit summary" })).toBeVisible();
});

test("Job vs CV: the ad beside the CV, keywords highlighted as the ATS card counts them (ATSC-08, 20 Nov)", async ({ page }) => {
  await page.goto("/review");
  await page.getByRole("link", { name: "Graduate AI Engineer" }).click();
  await expect(page.getByText("Gate 2 · needs your approval")).toBeVisible();
  // ATSC-09: the Writing card sits in the rail on every tab, and scores nothing.
  await expect(page.getByRole("heading", { name: "Writing" })).toBeVisible();

  await page.getByRole("tab", { name: "Job vs CV" }).click();
  await expect(page.getByRole("heading", { name: "Job ad", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Your CV", exact: true })).toBeVisible();
  const key = page.getByRole("list", { name: "Colour key" });
  await expect(key).toContainText("On your CV");
  await expect(key).toContainText("Not in your CV");
  await expect(page.getByRole("progressbar", { name: "Keyword match" })).toBeVisible();
  // The stats bar's percentage is the ATS card's coverage, to the point.
  const card = await page.getByRole("progressbar", { name: "ATS keyword coverage" }).getAttribute("aria-valuenow");
  await expect(page.getByRole("progressbar", { name: "Keyword match" })).toHaveAttribute("aria-valuenow", card!);
  await expect(page.locator("mark").first()).toBeVisible();
});

test("Learn about the company, then rewrite the letter with it (18 Nov)", async ({ page }) => {
  await page.goto("/review");
  await page.getByRole("link", { name: "Graduate AI Engineer" }).click();
  await page.getByRole("tab", { name: "Company" }).click();
  await page.getByRole("button", { name: "Learn about Saltmarsh Analytics" }).click();

  // Demo mode reads no web pages: the brief is the post's, says so, and every item shows its words.
  await expect(page.getByRole("heading", { name: "What WERA learned about Saltmarsh Analytics" })).toBeVisible();
  await expect(page.getByText("From the job post alone.")).toBeVisible();
  await expect(page.getByText("Demo mode reads no web pages, so this brief is from the post alone.")).toBeVisible();
  await expect(page.getByText(/— the job post/).first()).toBeVisible();

  await page.getByRole("button", { name: "Rewrite the letter with this brief" }).click();
  await expect(page.getByText("Gate 2 · needs your approval")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("tab", { name: "Cover letter" }).click();
  await expect(page.getByText("Dear Hiring Team,")).toBeVisible();
  await page.getByRole("tab", { name: "Company" }).click();
  await expect(page.getByRole("heading", { name: "What WERA learned about Saltmarsh Analytics" })).toBeVisible();
});

test("A job's post reads in sections, with its blurb folded, and summarises on click (18 Nov)", async ({ page }) => {
  await page.goto("/jobs");
  const card = page.getByRole("region", { name: "Junior AI Engineer" });
  await card.getByRole("button", { name: "Show details" }).click();

  // Laid out from the post: its sections in order, the company's own blurb one click away.
  await expect(card.getByRole("heading", { name: "Requirements", level: 4 })).toBeVisible();
  const blurb = card.locator("details").filter({ hasText: "About Kestrel Health" });
  await expect(blurb).toBeVisible();
  await expect(blurb).not.toHaveAttribute("open", "");

  await card.getByRole("button", { name: "Summary" }).click();
  await expect(card.getByRole("heading", { name: "Must have", level: 4 })).toBeVisible();
  await expect(card.getByText("checked against it: anything the post doesn't say was left out", { exact: false })).toBeVisible();
  // Kept: back to the post and to the summary again, with no second request.
  await card.getByRole("button", { name: "Full post" }).click();
  await expect(card.getByRole("heading", { name: "Requirements", level: 4 })).toBeVisible();
  await card.getByRole("button", { name: "Summary" }).click();
  await expect(card.getByRole("heading", { name: "Must have", level: 4 })).toBeVisible();
});

test("A LinkedIn link is never fetched; the description is asked for instead", async ({ page }) => {
  const imports: string[] = [];
  page.on("request", (r) => r.url().includes("/api/jobs/import") && imports.push(r.url()));
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Already have a link? Paste a job instead" }).click();
  await page.getByLabel("Job link").fill("https://www.linkedin.com/jobs/view/4100000009");
  await page.getByRole("button", { name: "Import job" }).click();
  await expect(page.getByText("WERA never opens LinkedIn links.")).toBeVisible();
  await expect(page.getByLabel("Job description")).toBeVisible();
  expect(imports).toEqual([]);
});

test("The search profile can't be saved without a title (PRF-03)", async ({ page }) => {
  await page.goto("/jobs");
  await page.getByRole("button", { name: "Edit profile" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit search profile" });
  for (const t of ["AI Engineer", "Graduate Software Engineer", "ML Engineer", "Backend Engineer (Python)"]) {
    await dialog.getByRole("button", { name: `Remove ${t}`, exact: true }).click();
  }
  await dialog.getByRole("button", { name: "Save profile" }).click();
  await expect(dialog.getByText("Add at least 1 target title")).toBeVisible();
});

test("Upskills: only what I'm missing, and charts that filter the page (UPSK-04, 20 Nov)", async ({ page }) => {
  await page.goto("/upskills");
  await expect(page.getByRole("heading", { name: "Upskills", level: 1 })).toBeVisible();
  const demand = page.getByRole("region", { name: /matter most in these jobs/ });
  await expect(demand.getByRole("img", { name: /^Python:/ })).toBeVisible(); // the demo CV lists Python

  const onlyMissing = page.getByRole("switch", { name: "Only what I'm missing" });
  await onlyMissing.click();
  await expect(onlyMissing).toHaveAttribute("aria-checked", "true");
  await expect(page).toHaveURL(/view=missing/);
  await expect(demand.getByRole("img", { name: /^Python:/ })).toHaveCount(0); // covered skills are hidden

  // A gap's bar jumps to its row in the table.
  await demand.getByRole("button", { name: /^Docker:/ }).click();
  await expect(page.locator("#skill-docker")).toHaveAttribute("aria-current", "true");

  // A category bar scopes the page to that kind of skill; a second click shows them all again.
  const other = page.getByRole("region", { name: "Where the gap is" }).getByRole("button", { name: /^Other:/ });
  await other.click();
  await expect(other).toHaveAttribute("aria-pressed", "true");
  await expect(page).toHaveURL(/category=other/);

  // The view is in the URL, so it survives a reload.
  await page.reload();
  await expect(page.getByRole("switch", { name: "Only what I'm missing" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByRole("button", { name: "Other", exact: true })).toHaveAttribute("aria-pressed", "true");
});

// Last: it throws away a pack the journeys above read.
test("Discarding a pack asks why, as a reject does (MATCH-11, 19 Nov)", async ({ page }) => {
  await page.goto("/review");
  await page.getByRole("link", { name: "Graduate AI Engineer" }).click();
  await page.getByRole("button", { name: "Discard", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Discard this pack?" });
  await dialog.getByRole("button", { name: "Not my field" }).click();
  await expect(dialog.getByRole("button", { name: "Not my field" })).toHaveAttribute("aria-pressed", "true");
  await dialog.getByRole("button", { name: "Discard" }).click();
  await expect(page).toHaveURL(/\/review$/);
  await expect(page.getByRole("link", { name: "Graduate AI Engineer" })).toHaveCount(0);
});

test("More found, not read yet: expand the previews, read one, and it is scored on the shortlist (H3, 21 Nov)", async ({ page }) => {
  await page.goto("/jobs");
  await expect(page.getByRole("heading", { name: "Today's shortlist" })).toBeVisible();

  // Collapsed under the scored jobs: a real button, and nothing of the previews until it opens.
  const toggle = page.getByRole("button", { name: /^More found, not read yet \(\d+\)/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("article", { name: "Graduate Software Engineer, Payments" })).toHaveCount(0);
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");

  // Best match first, with its label, the employer "via" the job API, and Adzuna's name linked.
  const preview = page.getByRole("article", { name: "Graduate Software Engineer, Payments" });
  await expect(preview).toBeVisible();
  await expect(preview.getByText("Strong match", { exact: true })).toBeVisible();
  await expect(preview.getByText("Larkspur Payments · via Adzuna", { exact: false })).toBeVisible();
  await expect(preview.getByRole("link", { name: "Jobs by Adzuna" })).toHaveAttribute("href", "https://www.adzuna.co.uk");
  await expect(preview.getByRole("button", { name: "Snooze", exact: true })).toBeVisible();
  await expect(preview.getByRole("button", { name: "Reject", exact: true })).toBeVisible();

  // Read it: the preview leaves the section and the scored job joins the shortlist.
  await preview.getByRole("button", { name: "Read the full ad" }).click();
  await expect(page.getByText("Read the full ad for Graduate Software Engineer, Payments: fit", { exact: false })).toBeVisible();
  await expect(page.getByRole("article", { name: "Graduate Software Engineer, Payments" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Shortlisted jobs" }).getByRole("heading", { level: 3, name: "Graduate Software Engineer, Payments" })).toBeVisible();
});
