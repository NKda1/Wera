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
  await expect(cards(page)).toHaveCount(12);
  await expect(page.getByText("8.6", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Search now" }).click();
  const dialog = page.getByRole("dialog", { name: "Search now" });
  await expect(dialog.getByText(/20 queries/)).toBeVisible();
  await dialog.getByRole("button", { name: "Run search" }).click();
  await expect(page.getByRole("heading", { name: "Running your search…" })).toBeVisible();
  await expect(page.getByText("Search finished: 2 new jobs added.")).toBeVisible();
  await expect(cards(page)).toHaveCount(14);

  await page.getByRole("button", { name: "Score 8+" }).click();
  await expect(cards(page)).toHaveCount(2);
  await page.getByRole("button", { name: /^All/ }).click();

  const fernhill = page.getByRole("region", { name: "ML Engineer, Graduate" });
  await fernhill.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Approved ML Engineer, Graduate. Drafting your pack…")).toBeVisible();
  await expect(cards(page)).toHaveCount(13);

  const harbour = page.getByRole("region", { name: "Graduate Data Engineer" });
  await harbour.getByRole("button", { name: "Snooze" }).click();
  await page.getByRole("menuitem", { name: "3 days" }).click();
  await expect(cards(page)).toHaveCount(12);
});

test("J3: review the drafted pack, approve it and track it", async ({ page }) => {
  await page.goto("/review");
  const row = page.getByRole("link", { name: "ML Engineer, Graduate" });
  await expect(row).toBeVisible();
  await row.click();

  await expect(page.getByText("Gate 2 · needs your approval")).toBeVisible();
  await expect(page.getByText("86%")).toBeVisible();
  await expect(page.getByText("Docker", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download PDF" })).toBeDisabled();

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
  await expect(page.getByRole("button", { name: "Download PDF" })).toBeEnabled();

  await page.getByRole("button", { name: "I've submitted it" }).click();
  await expect(page.getByText(/Submitted \d/)).toBeVisible();

  await page.goto("/tracker");
  await expect(page.getByRole("region", { name: "Submitted" })).toContainText("ML Engineer, Graduate");
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
