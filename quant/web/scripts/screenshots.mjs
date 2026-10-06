// Screenshot the built app served by FastAPI (or the vite dev server).
// Usage: node scripts/screenshots.mjs [baseUrl] [outDir] [paths...]
//   defaults: http://localhost:8090, ../../docs/media/quant/paper, every route.
// Writes <outDir>/<page>/<page>-<width>-<theme>.png at 1440 and 380 in paper and carbon,
// and fails if any page scrolls sideways at 380 or throws.
// Chromium: CHROMIUM_PATH, else PLAYWRIGHT_BROWSERS_PATH (default /opt/pw-browsers), else Playwright's own.
import { chromium } from "playwright";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

const ROUTES = ["/", "/markets", "/risk", "/portfolio", "/optimize", "/research", "/options", "/macro", "/company/AAPL", "/ticker/SPY", "/deck", "/system", "/compute", "/engine", "/methodology", "/ledger"];

const base = process.argv[2] ?? "http://localhost:8090";
const out = process.argv[3] ?? "../../docs/media/quant/paper";
const paths = process.argv.slice(4).length ? process.argv.slice(4) : ROUTES;

function findChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH ?? "/opt/pw-browsers";
  if (!existsSync(root)) return undefined;
  for (const d of readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()) {
    for (const rel of ["chrome-linux/chrome", "chrome-linux64/chrome"]) {
      const p = join(root, d, rel);
      if (existsSync(p)) return p;
    }
  }
  return undefined;
}

const slugOf = (p) => (p === "/" ? "front" : p.replace(/^\//, "").replace(/[/?=&]+/g, "-").toLowerCase());

const viewports = [
  { width: 1440, height: 900 },
  { width: 380, height: 844, isMobile: true, deviceScaleFactor: 2 },
];
const failures = [];
const browser = await chromium.launch({ executablePath: findChromium() });
for (const theme of ["paper", "carbon"]) {
  for (const vp of viewports) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: vp.deviceScaleFactor ?? 1, isMobile: vp.isMobile ?? false, colorScheme: theme === "carbon" ? "dark" : "light" });
    await ctx.addInitScript((t) => localStorage.setItem("ohcamel.theme", t), theme);
    const page = await ctx.newPage();
    page.on("pageerror", (e) => failures.push(`pageerror ${theme} ${vp.width} ${page.url()}: ${e.message}`));
    for (const p of paths) {
      await page.goto(base + p, { waitUntil: "networkidle" });
      await page.waitForTimeout(1500);
      if (vp.width < 768) {
        const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
        if (sw > iw) failures.push(`overflow ${theme} ${vp.width} ${p}: scrollWidth ${sw} > ${iw}`);
      }
      const slug = slugOf(p);
      mkdirSync(join(out, slug), { recursive: true });
      const file = join(out, slug, `${slug}-${vp.width}-${theme}.png`);
      await page.screenshot({ path: file, fullPage: true });
      console.log("saved", file);
    }
    await ctx.close();
  }
}
await browser.close();
if (failures.length) {
  console.error(`screenshots: ${failures.length} problem${failures.length > 1 ? "s" : ""}`);
  for (const f of failures) console.error("  " + f);
  process.exit(1);
}
