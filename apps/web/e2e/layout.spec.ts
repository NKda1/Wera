import { expect, test, type Page } from "@playwright/test";

// A11Y-02 at every width, not one: each screen from a small phone to a wide desktop. 1024 is in the
// list on purpose — the sidebar arrives there and leaves the content narrower (656px) than on a
// 768px tablet (688px), which is where the jobs filters used to run under the sort switch.
const SIZES = [
  { width: 360, height: 740 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 900 },
];
const SCREENS = ["/jobs", "/review", "/cv", "/tracker", "/settings", "/upskills"];

async function noSidewaysScroll(page: Page) {
  await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
  expect(scroll, "the page scrolls sideways").toBeLessThanOrEqual(client);
}

for (const size of SIZES) {
  test.describe(`at ${size.width}px`, () => {
    test.use({ viewport: size });

    for (const path of SCREENS) {
      test(`${path} has no sideways scroll`, async ({ page }) => {
        await page.goto(path);
        await noSidewaysScroll(page);
      });
    }

    test("a pack under review has no sideways scroll", async ({ page }) => {
      await page.goto("/review");
      const href = await page.locator('main a[href^="/review/"]').first().getAttribute("href");
      expect(href).toBeTruthy();
      await page.goto(href!);
      await noSidewaysScroll(page);
    });

    test("no jobs filter is hidden or under the sort switch", async ({ page }) => {
      await page.goto("/jobs");
      const filters = page.getByRole("group", { name: "Filter jobs" });
      const sort = page.getByRole("radiogroup", { name: "Sort" });
      await expect(sort).toBeVisible();
      const box = await sort.boundingBox();
      for (const chip of await filters.getByRole("button").all()) {
        const c = await chip.boundingBox();
        if (!c || !box) continue;
        const overlaps = c.x < box.x + box.width && box.x < c.x + c.width && c.y < box.y + box.height && box.y < c.y + c.height;
        expect(overlaps, `${await chip.textContent()} sits under the sort switch`).toBe(false);
      }
      // The dropdowns wrap rather than scroll, at every width: a scrolling row would clip the
      // popover that opens under a button. None may be clipped out of sight.
      const { scroll, client } = await filters.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
      expect(scroll, "filter buttons are clipped").toBeLessThanOrEqual(client);
    });
  });
}

test.describe("the jobs filters at 1440px", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("sit on one row", async ({ page }) => {
    await page.goto("/jobs");
    const menus = page.getByRole("group", { name: "Filter jobs" }).locator("button[aria-haspopup]");
    await expect(menus.first()).toBeVisible();
    expect(await menus.count(), "expected several filter dropdowns").toBeGreaterThan(1);
    const ys = await Promise.all((await menus.all()).map(async (m) => (await m.boundingBox())!.y));
    expect(new Set(ys).size, `dropdowns at y = ${ys.join(", ")}`).toBe(1);
  });
});

test.describe("the phone top bar", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("is one row", async ({ page }) => {
    await page.goto("/jobs");
    const bar = await page.getByRole("banner").boundingBox();
    expect(bar!.height).toBeLessThan(72);
  });

  test("shows the current section even when it is the last one", async ({ page }) => {
    await page.goto("/settings");
    const banner = page.getByRole("banner");
    const current = banner.locator('[aria-current="page"]');
    await expect(current).toHaveText("Settings");
    const [b, c] = [await banner.boundingBox(), await current.boundingBox()];
    expect(c!.x + c!.width).toBeLessThanOrEqual(b!.x + b!.width);
    expect(c!.x).toBeGreaterThanOrEqual(b!.x);
  });
});

test.describe("milestone 2 screens on a phone", () => {
  test.use({ viewport: { width: 360, height: 740 } });

  test("a source tag's original line opens on screen", async ({ page }) => {
    await page.goto("/review");
    await page.goto((await page.locator('main a[href^="/review/"]').first().getAttribute("href"))!);
    await page.getByRole("button", { name: /^Source .*: show the original line$/ }).first().click();
    const pop = await page.getByRole("dialog", { name: /^Original line/ }).boundingBox();
    expect(pop!.x).toBeGreaterThanOrEqual(0);
    expect(pop!.x + pop!.width).toBeLessThanOrEqual(360);
  });

  test("a settings key row keeps room for its text", async ({ page }) => {
    await page.goto("/settings");
    const name = page.getByText("LLM_API_KEY", { exact: true }).first();
    expect((await name.boundingBox())!.width).toBeGreaterThan(80);
  });
});

test("the tracker's column strip brings the last column into view", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/tracker");
  const closed = page.getByRole("region", { name: /^Closed/ });
  await page.getByRole("navigation", { name: "Jump to a column" }).getByRole("button", { name: /^Closed/ }).click();
  await expect(async () => {
    const box = (await closed.boundingBox())!;
    expect(box.x + box.width).toBeLessThanOrEqual(1024);
    expect(box.x).toBeGreaterThanOrEqual(0);
  }).toPass();
});

test("a pack's checks sit beside the document only when there is room", async ({ page }) => {
  await page.goto("/review");
  const href = (await page.locator('main a[href^="/review/"]').first().getAttribute("href"))!;
  for (const [width, beside] of [
    [1440, true],
    [1024, false],
  ] as const) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(href);
    const doc = (await page.getByRole("tabpanel").boundingBox())!;
    const rail = (await page.getByRole("complementary", { name: "Checks" }).boundingBox())!;
    expect(rail.x > doc.x + doc.width - 1, `checks beside the document at ${width}px`).toBe(beside);
  }
});
