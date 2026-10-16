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
