// Bundle guard, run by `npm run build` after `vite build`.
// Reads dist/.vite/manifest.json (vite.config.ts sets build.manifest) and:
//   - fails if the entry chunk is over 250 KB gzip (zlib.gzipSync of the emitted file);
//   - fails if Plotly is reachable from the entry through static imports (it must only be
//     fetched by loadPlotly()'s dynamic import());
//   - strict (Ship plan gate GP2): fails if any chunk other than the surface view
//     (src/pages/volatility/SurfaceView.tsx, the 3-D implied-vol surface) imports Plotly. P2-7
//     migrated the last raw Plotly <Chart> (the Engine page) and deleted it with its loader;
//   - fails if any other source file renders a raw <Chart>, imports Plotly or calls loadPlotly.
// Usage: node scripts/check-bundle.mjs [distDir] [srcDir]   (defaults ./dist and ./src)
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { gzipSync } from "node:zlib";

const dist = process.argv[2] ?? "dist";
const src = process.argv[3] ?? "src";
const SURFACE = "src/pages/volatility/SurfaceView.tsx";
const LIMIT = 250 * 1000;
const manifest = JSON.parse(readFileSync(join(dist, ".vite", "manifest.json"), "utf8"));
const gz = (file) => gzipSync(readFileSync(join(dist, file))).length;
const kb = (n) => `${(n / 1000).toFixed(1)} KB`;

const entries = Object.entries(manifest).filter(([, c]) => c.isEntry);
if (entries.length !== 1) {
  console.error(`check-bundle: expected one entry chunk, found ${entries.length}`);
  process.exit(1);
}
const [entryKey, entry] = entries[0];
const failures = [];

const entrySize = gz(entry.file);
if (entrySize > LIMIT) failures.push(`entry ${entry.file} is ${kb(entrySize)} gzip (limit ${kb(LIMIT)})`);

// Static closure of the entry.
const seen = new Set();
const stack = [entryKey];
while (stack.length) {
  const k = stack.pop();
  if (seen.has(k) || !manifest[k]) continue;
  seen.add(k);
  stack.push(...(manifest[k].imports ?? []));
}
// The Plotly chunk itself (vite.config.ts manualChunks "plotly"), not modules merely named after it.
const isPlotly = (k, c) => c.name === "plotly" || /plotly\.js-dist-min/.test(k) || /(^|\/)plotly-[A-Za-z0-9_-]+\.js$/.test(c.file);
const plotlyKeys = Object.entries(manifest).filter(([k, c]) => isPlotly(k, c)).map(([k]) => k);
for (const k of plotlyKeys) if (seen.has(k)) failures.push(`Plotly (${manifest[k].file}) is statically reachable from the entry`);

// Strict: the surface view alone.
for (const [k, c] of Object.entries(manifest)) {
  const imports = [...(c.imports ?? []), ...(c.dynamicImports ?? [])];
  if (!imports.some((i) => plotlyKeys.includes(i)) || isPlotly(k, c)) continue;
  if (c.src === SURFACE) continue;
  failures.push(`${c.file}${c.src ? ` (${c.src})` : ` (${k})`} imports Plotly; only the surface view (${SURFACE}) may`);
}
if (!Object.values(manifest).some((c) => c.src === SURFACE && [...(c.imports ?? []), ...(c.dynamicImports ?? [])].some((i) => plotlyKeys.includes(i))))
  failures.push(`the surface view (${SURFACE}) is not its own chunk importing Plotly: load it with React.lazy`);

// Source: no raw <Chart> and no Plotly import outside the surface view.
function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) yield* walk(full);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) yield full;
  }
}
for (const file of walk(src)) {
  const rel = ["src", ...relative(src, file).split(sep)].join("/");
  if (rel === SURFACE) continue;
  const text = readFileSync(file, "utf8");
  if (/<Chart[\s>]/.test(text)) failures.push(`${rel} renders a raw Plotly <Chart>; use the uPlot wrappers in src/charts/`);
  // the runtime package (plotly.js-dist-min); "plotly.js" is its types package, type-only
  if (/["']plotly\.js-dist-min["']/.test(text)) failures.push(`${rel} imports Plotly; only ${SURFACE} may`);
  if (/\bloadPlotly\b/.test(text)) failures.push(`${rel} calls loadPlotly; only ${SURFACE} may`);
}

const closure = [...seen].reduce((a, k) => a + gz(manifest[k].file), 0);
console.log(`check-bundle: entry ${entry.file} ${kb(entrySize)} gzip; with static imports ${kb(closure)} gzip`);
const importers = Object.entries(manifest).filter(([, c]) => [...(c.imports ?? []), ...(c.dynamicImports ?? [])].some((k) => plotlyKeys.includes(k)));
console.log(`check-bundle: chunks importing Plotly (${importers.length}):`);
for (const [k, c] of importers) console.log(`  ${c.file}${c.src ? ` (${c.src})` : ` (${k})`}${(c.imports ?? []).some((i) => plotlyKeys.includes(i)) ? " static" : " dynamic"}`);

if (failures.length) {
  console.error(`check-bundle: ${failures.length} failure${failures.length > 1 ? "s" : ""}`);
  for (const f of failures) console.error("  " + f);
  process.exit(1);
}
console.log("check-bundle: ok");
