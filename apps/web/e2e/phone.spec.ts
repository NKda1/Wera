import { expect, test } from "@playwright/test";

// A11Y-02: reading and approving work at 390 px with no sideways scroll.
for (const path of ["/jobs", "/review", "/cv", "/tracker", "/settings"]) {
  test(`${path} fits a 390 px screen`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("banner").getByText("Demo mode")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
}

test("a job can be approved on a phone", async ({ page }) => {
  await page.goto("/jobs");
  await page.getByRole("region", { name: "Software Engineer, AI Platform" }).getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText(/Approved Software Engineer, AI Platform/)).toBeVisible();
});

test("a pack's lines can be edited on a touch screen", async ({ page }) => {
  // The pencil is hidden until hovered only where something can hover; a phone never does.
  await page.goto("/review");
  await page.goto((await page.locator('main a[href^="/review/"]').first().getAttribute("href"))!);
  const pencil = page.getByRole("button", { name: /^Edit / }).first();
  await expect(pencil).toBeVisible();
  expect(Number(await pencil.evaluate((el) => getComputedStyle(el).opacity))).toBeGreaterThan(0.5);
});
