// Screenshot the built app served by FastAPI (or the vite dev server).
// Usage: node scripts/screenshots.mjs [baseUrl] [outDir] [paths...]
//   defaults: http://localhost:8090, ../../docs/media/quant/paper, every route.
// Writes <outDir>/<page>/<page>-<width>-<theme>.png at 1440 and 380 in paper and carbon,
// and fails if any page scrolls sideways at 380, throws, or has a serious or critical axe
// violation (axe-core, every width and theme). On migrated routes (RHYTHM_ROUTES) it also fails
// when the body rows of a dense table are not all one height: a broken row rhythm.
// Chromium: CHROMIUM_PATH, else PLAYWRIGHT_BROWSERS_PATH (default /opt/pw-browsers), else Playwright's own.
import { chromium } from "playwright";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const ROUTES = ["/", "/markets", "/risk", "/portfolio", "/optimize", "/research", "/options", "/macro", "/company/AAPL", "/ticker/SPY", "/deck", "/system", "/compute", "/engine", "/methodology", "/ledger"];

// Paper Tape pages whose dense tables must keep one row height. Later P2 tasks add their routes.
const RHYTHM_ROUTES = new Set(["/markets"]);

async function unevenRows(page) {
  return page.evaluate(() => {
    const bad = [];
    document.querySelectorAll("table.oc-table-compact").forEach((t, i) => {
      // The first cell's padding box: a row's tr box also carries half of the collapsed header rule.
      const hs = [...t.querySelectorAll("tbody tr")].map((r) => r.firstElementChild?.clientHeight ?? 0).filter((h) => h > 0);
      if (hs.length < 2) return;
      const lo = Math.min(...hs), hi = Math.max(...hs);
      if (hi > lo) bad.push(`table ${i}: rows ${lo}-${hi}px`);
    });
    return bad;
  });
}

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

const AXE = createRequire(import.meta.url).resolve("axe-core/axe.min.js");

async function axeSerious(page) {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    const r = await window.axe.run(document, { resultTypes: ["violations"] });
    return r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => `${v.id} (${v.nodes.length}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`);
  });
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
      if (RHYTHM_ROUTES.has(p)) for (const v of await unevenRows(page)) failures.push(`rhythm ${theme} ${vp.width} ${p}: ${v}`);
      for (const v of await axeSerious(page)) failures.push(`axe ${theme} ${vp.width} ${p}: ${v}`);
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
