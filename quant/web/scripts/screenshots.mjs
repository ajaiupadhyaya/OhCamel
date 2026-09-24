// Screenshot the built app served by FastAPI.
// Usage: node scripts/screenshots.mjs [baseUrl] [outDir] [paths...]
// Needs Chromium: PLAYWRIGHT_BROWSERS_PATH (default /opt/pw-browsers) or CHROMIUM_PATH.
import { chromium } from "playwright";
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";

const base = process.argv[2] ?? "http://localhost:8090";
const out = process.argv[3] ?? "screenshots";
const paths = process.argv.slice(4).length ? process.argv.slice(4) : ["/", "/ticker/SPY"];

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

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844, isMobile: true, deviceScaleFactor: 2 },
];
const browser = await chromium.launch({ executablePath: findChromium() });
for (const theme of ["light", "dark"]) {
  for (const vp of viewports) {
    const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: vp.deviceScaleFactor ?? 1, isMobile: vp.isMobile ?? false, colorScheme: theme });
    await ctx.addInitScript((t) => localStorage.setItem("ohcamel.theme", t), theme);
    const page = await ctx.newPage();
    page.on("pageerror", (e) => console.error(`[pageerror ${theme} ${vp.name}]`, e.message));
    for (const p of paths) {
      await page.goto(base + p, { waitUntil: "networkidle" });
      await page.waitForTimeout(1500);
      const slug = p === "/" ? "markets" : p.replace(/^\//, "").replace(/[/?=&]+/g, "-").toLowerCase();
      const file = join(out, `${slug}-${vp.name}-${theme}.png`);
      await page.screenshot({ path: file, fullPage: true });
      console.log("saved", file);
    }
    await ctx.close();
  }
}
await browser.close();
