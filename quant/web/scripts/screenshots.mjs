// Screenshot the built app served by FastAPI (or the vite dev server).
// Usage: node scripts/screenshots.mjs [baseUrl] [outDir] [paths...]
//   defaults: http://localhost:8090, ../../docs/media/quant/paper, every route.
// Writes <outDir>/<page>/<page>-<width>-<theme>.png at 1440 and 380 in paper and carbon,
// and fails if any page scrolls sideways at 380, throws, or has a serious or critical axe
// violation (axe-core, every width and theme). On migrated routes (RHYTHM_ROUTES) it also fails
// when the body rows of a dense table are not all one height: a broken row rhythm.
// "/" is The Machine, which is always dark and has its own shots: whenever "/" is among the routes it is shot
// again at MACHINE_SHOTS' four tour steps, 1440 x 900 and 375 x 812, into <outDir>/../machine/machine-step<n>-<width>.png,
// each after its canvas has drawn and 2.5 s have passed. Those shots get the same checks, and a shot that cannot be
// taken (no canvas, because WebGL did not start) is a failure like any other. The tape's own paper shots are /tape's.
// Chromium: CHROMIUM_PATH, else PLAYWRIGHT_BROWSERS_PATH (default /opt/pw-browsers), else Playwright's own.
import { chromium } from "playwright";
import { existsSync, mkdirSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";

const ROUTES = ["/", "/tape", "/markets", "/risk", "/portfolio", "/optimize", "/optimize?tab=backtest", "/research", "/options", "/macro", "/company/AAPL", "/ticker/SPY", "/deck", "/system", "/compute", "/engine", "/methodology", "/ledger"];

// Paper Tape pages whose dense tables must keep one row height. Later P2 tasks add their routes.
const RHYTHM_ROUTES = new Set(["/markets", "/risk", "/portfolio", "/optimize", "/research", "/options", "/macro", "/ticker/SPY", "/company/AAPL", "/engine", "/compute", "/system"]);
// Not /methodology or /ledger: their tables are prose (sources, ledger detail) whose rows wrap by design.

// The machine's tour as deep links (spec 6.8): the overview, the price feed, the strategy farm and the book.
const MACHINE_SHOTS = ["/?step=1", "/?step=2", "/?step=5", "/?step=7"];
const MACHINE_VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 375, height: 812, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
];
const MACHINE_SETTLE_MS = 2500;
// Routes whose paper and carbon shots are not taken: "/" is the machine, and its shots are MACHINE_SHOTS. It is still
// loaded at both widths and both themes for the sideways-scroll and axe checks.
const NO_PAPER_SHOT = new Set(["/"]);

// Routes that need a step before the shot so a populated state is visible. Offline, the walk-forward
// backtest has no 3M bill (no DGS3MO fixture) and reads INSUFFICIENT DATA; with the rail's MANUAL
// risk-free rate (2%, shown as such on the page) it runs on the committed fixture prices, which puts
// the ΔSR forest with its "p · HOLM" gutter, growth, drawdown and the scorecard in the shot.
const PREPARE = {
  // The machine's instrument is a lazy chunk: the page is ready when its canvas and its first label are there.
  "/": async (page) => machineReady(page),
  "/optimize?tab=backtest": async (page) => {
    await page.getByRole("radio", { name: "MANUAL" }).click();
    await page.getByRole("button", { name: "RUN", exact: true }).click();
    await page.waitForSelector(".oc-chart-coef svg", { timeout: 60000 });
    await page.waitForLoadState("networkidle");
  },
};

async function machineReady(page) {
  await page.waitForSelector("canvas", { timeout: 60000 });
  await page.waitForSelector(".mx-lb", { timeout: 60000 });
}

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
// Headless WebGL (the machine) needs the software rasteriser, which Chromium keeps behind these two flags.
const browser = await chromium.launch({ executablePath: findChromium(), args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
for (const theme of ["paper", "carbon"]) {
  for (const vp of viewports) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: vp.deviceScaleFactor ?? 1, isMobile: vp.isMobile ?? false, colorScheme: theme === "carbon" ? "dark" : "light" });
    await ctx.addInitScript((t) => localStorage.setItem("ohcamel.theme", t), theme);
    const page = await ctx.newPage();
    page.on("pageerror", (e) => failures.push(`pageerror ${theme} ${vp.width} ${page.url()}: ${e.message}`));
    for (const p of paths) {
      await page.goto(base + p, { waitUntil: "networkidle" });
      await page.waitForTimeout(1500);
      if (PREPARE[p]) await PREPARE[p](page);
      if (vp.width < 768) {
        const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
        if (sw > iw) failures.push(`overflow ${theme} ${vp.width} ${p}: scrollWidth ${sw} > ${iw}`);
      }
      if (RHYTHM_ROUTES.has(p.split("?")[0])) for (const v of await unevenRows(page)) failures.push(`rhythm ${theme} ${vp.width} ${p}: ${v}`);
      for (const v of await axeSerious(page)) failures.push(`axe ${theme} ${vp.width} ${p}: ${v}`);
      if (NO_PAPER_SHOT.has(p)) continue;
      const slug = slugOf(p);
      mkdirSync(join(out, slug), { recursive: true });
      const file = join(out, slug, `${slug}-${vp.width}-${theme}.png`);
      await page.screenshot({ path: file, fullPage: true });
      console.log("saved", file);
    }
    await ctx.close();
  }
}

// The machine's tour: dark always (the page has one look), so no theme loop. A shot that fails is recorded and the
// rest are still taken, and the script exits non-zero at the end.
if (paths.includes("/")) {
  const machineDir = join(out, "..", "machine");
  mkdirSync(machineDir, { recursive: true });
  for (const vp of MACHINE_VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: vp.deviceScaleFactor ?? 1, isMobile: vp.isMobile ?? false, hasTouch: vp.hasTouch ?? false, colorScheme: "dark" });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => failures.push(`pageerror machine ${vp.width} ${page.url()}: ${e.message}`));
    for (const p of MACHINE_SHOTS) {
      const step = /[?&]step=(\d+)/.exec(p)?.[1];
      try {
        await page.goto(base + p, { waitUntil: "networkidle" });
        await machineReady(page);
        await page.waitForTimeout(MACHINE_SETTLE_MS);
        if (vp.width < 768) {
          const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
          if (sw > iw) failures.push(`overflow machine ${vp.width} ${p}: scrollWidth ${sw} > ${iw}`);
        }
        for (const v of await axeSerious(page)) failures.push(`axe machine ${vp.width} ${p}: ${v}`);
        const file = join(machineDir, `machine-step${step}-${vp.width}.png`);
        await page.screenshot({ path: file });
        console.log("saved", file);
      } catch (e) {
        failures.push(`shot machine ${vp.width} ${p}: ${e instanceof Error ? e.message.split("\n")[0] : String(e)}`);
      }
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
