// Bundle guard, run by `npm run build` after `vite build`.
// Reads dist/.vite/manifest.json (vite.config.ts sets build.manifest) and:
//   - fails if the entry chunk is over 250 KB gzip (zlib.gzipSync of the emitted file);
//   - fails if Plotly is reachable from the entry through static imports (it must only be
//     fetched by loadPlotly()'s dynamic import());
//   - prints every chunk that imports the Plotly chunk (informational in Lane P Task P5;
//     the P2 gate makes "only the surface view" strict).
// Usage: node scripts/check-bundle.mjs [distDir]   (distDir defaults to ./dist)
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { gzipSync } from "node:zlib";

const dist = process.argv[2] ?? "dist";
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
const isPlotly = (k, c) => c.name === "plotly" || /plotly/i.test(c.file) || /plotly\.js-dist-min/.test(k);
const plotlyKeys = Object.entries(manifest).filter(([k, c]) => isPlotly(k, c)).map(([k]) => k);
for (const k of plotlyKeys) if (seen.has(k)) failures.push(`Plotly (${manifest[k].file}) is statically reachable from the entry`);

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
