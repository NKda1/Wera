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

  await page.getByRole("group", { name: "Fit" }).getByRole("button", { name: /^8\+/ }).click();
  await expect(cards(page)).toHaveCount(2);
  await page.getByRole("button", { name: /^All/ }).click();

  const fernhill = page.getByRole("region", { name: "ML Engineer, Graduate" });
  await fernhill.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Approved ML Engineer, Graduate. Drafting your pack…")).toBeVisible();
  await expect(cards(page)).toHaveCount(11);

  const harbour = page.getByRole("region", { name: "Graduate Data Engineer" });
  await harbour.getByRole("button", { name: "Snooze" }).click();
  await page.getByRole("menuitem", { name: "3 days" }).click();
  await expect(cards(page)).toHaveCount(10);
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
