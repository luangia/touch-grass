/**
 * README screenshots from the running local app (docs/screenshots/*.png).
 *   (API on :8000 and `npm run dev` in web/ on :5173, with Portland loaded)
 *   node screenshots.mjs
 */
import { chromium } from "@playwright/test";

const WEB = process.env.WEB_URL ?? "http://localhost:5173";
const OUT = new URL("../docs/screenshots/", import.meta.url);
const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

const shots = [
  {
    name: "map-residents-light",
    path: "/?view=residents&type=neighborhood&threshold=10",
    size: DESKTOP,
    scheme: "light",
  },
  {
    name: "map-blocks-dark",
    path: "/?view=blocks&type=neighborhood&threshold=10&area=neighborhood:16",
    size: DESKTOP,
    scheme: "dark",
  },
  {
    name: "rankings-light",
    path: "/rankings?type=neighborhood&threshold=10",
    size: DESKTOP,
    scheme: "light",
  },
  {
    name: "compare-light",
    path: "/compare?type=neighborhood&threshold=10&compare=neighborhood:87,neighborhood:16,city",
    size: DESKTOP,
    scheme: "light",
  },
  {
    name: "map-phone-light",
    path: "/?view=residents&type=neighborhood&threshold=10&area=neighborhood:95",
    size: PHONE,
    scheme: "light",
    mobile: true,
  },
];

const browser = await chromium.launch({
  args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"],
});
for (const s of shots) {
  const context = await browser.newContext({
    viewport: s.size,
    deviceScaleFactor: 1.5,
    colorScheme: s.scheme,
    isMobile: Boolean(s.mobile),
    hasTouch: Boolean(s.mobile),
  });
  const page = await context.newPage();
  await page.goto(`${WEB}${s.path}`);
  await page.getByRole("link", { name: "Rankings" }).waitFor();
  if (s.path.startsWith("/?") || s.path === "/") {
    await page.locator(".maplibregl-canvas").waitFor();
  }
  await page.waitForTimeout(5000); // camera fly-to and tile fade
  await page.screenshot({ path: new URL(`${s.name}.png`, OUT).pathname });
  console.log(`✓ ${s.name}`);
  await context.close();
}
await browser.close();
