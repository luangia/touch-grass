/**
 * Four user paths through the real app: map, Rankings, Compare, About (D-026 Q5).
 * Assertions are about content and data, never pixels. The basemap is replaced by an empty
 * style locally, so the test needs no outside network; the live run uses the real one.
 */
import { expect, type Page, test } from "@playwright/test";

const LIVE = Boolean(process.env.E2E_BASE_URL);
const BLANK_STYLE = { version: 8, sources: {}, layers: [], glyphs: undefined };

const errors: string[] = [];

test.beforeEach(async ({ page }) => {
  errors.length = 0;
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    // Failed requests are checked below, with their URL and type.
    if (
      m.type() === "error" &&
      !m.text().startsWith("Failed to load resource")
    ) {
      errors.push(m.text());
    }
  });
  page.on("response", (r) => {
    // GitHub Pages serves a deep link from 404.html with status 404: that page *is* the app
    // (D-026 Q3). Any other failed request (data, scripts, tiles) is an error.
    if (r.status() >= 400 && r.request().resourceType() !== "document") {
      errors.push(`${r.status()} ${r.url()}`);
    }
  });
  if (!LIVE) {
    await page.route("https://tiles.openfreemap.org/**", (route) =>
      route.fulfill({ json: BLANK_STYLE }),
    );
  }
});

test.afterEach(() => {
  expect(errors, "no errors in the browser console").toEqual([]);
});

async function openApp(page: Page, path = "") {
  await page.goto(path);
  // Allow for a sleeping host on the live site (the app retries for up to ~30 s).
  await expect(page.getByRole("link", { name: "Rankings" })).toBeVisible({
    timeout: 45_000,
  });
}

test("the map loads with the region's data", async ({ page }) => {
  await openApp(page);
  await expect(page.locator(".maplibregl-canvas")).toBeVisible();
  await expect(page.getByTestId("map")).toHaveAttribute("data-loaded", "true");
  await expect(page.getByRole("combobox")).toBeVisible(); // area search, filled from the API
  await expect(page.locator("body")).toContainText(/within a 10-minute walk/i);
});

test("the data still draws when the street map is blocked", async ({
  page,
}) => {
  // As with an ad blocker or a filtering network (a real report from a visitor).
  await page.unroute("https://tiles.openfreemap.org/**");
  await page.route("https://tiles.openfreemap.org/**", (route) =>
    route.abort("blockedbyclient"),
  );
  await openApp(page);
  await expect(page.getByTestId("map")).toHaveAttribute("data-loaded", "true");
  await expect(page.getByRole("status")).toContainText(
    "The street map couldn't load",
  );
  await expect(page.getByRole("alert")).toHaveCount(0); // a degraded view, not an error
});

test("Rankings shows four titled lists, and a deep link works", async ({
  page,
}) => {
  await openApp(page, "rankings");
  await expect(page.getByRole("heading", { level: 2 })).toHaveCount(4);
  await page.reload(); // a deep link served by the static host's fallback
  await expect(page.getByRole("heading", { level: 2 })).toHaveCount(4);
});

test("Compare shows a table for two areas", async ({ page }) => {
  await openApp(page, "compare");
  await page.getByRole("button", { name: "+ Whole city" }).click();
  // Type an area's exact name, as a user picking from the suggestions does.
  const name = await page
    .locator("datalist option")
    .first()
    .getAttribute("value");
  expect(name).toBeTruthy();
  await page.getByRole("combobox").fill(name!);
  await expect(page.getByRole("table")).toBeVisible();
  await expect(
    page.getByRole("img", { name: /Share of residents/ }),
  ).toBeVisible();
});

test("About lists every source with its licence", async ({ page }) => {
  await openApp(page, "about");
  await expect(page.locator("body")).toContainText("Open Database License");
  await expect(page.locator("body")).toContainText("Luan Pham");
});

test.describe("at phone width", () => {
  test.use({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });

  for (const path of ["", "rankings", "compare", "about"]) {
    test(`/${path} fits the screen without sideways scrolling`, async ({
      page,
    }) => {
      await openApp(page, path);
      const { content, screen } = await page.evaluate(() => ({
        content: document.documentElement.scrollWidth,
        screen: window.innerWidth,
      }));
      expect(content).toBeLessThanOrEqual(390);
      expect(screen).toBe(390);
    });
  }
});
