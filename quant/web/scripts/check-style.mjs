// Paper Tape style guard, run by `npm run build`.
// Scans src/**/*.{css,tsx,ts} (except the Flight Deck files and tests) and fails, naming
// file:line, on: a non-zero border-radius, a box-shadow other than none, any gradient()
// (CSS, SVG <linear/radialGradient>, canvas create*Gradient, Plotly fillgradient),
// the removed faces Fraunces and Inter Variable, and the green tokens --gain-soft and
// --crt-green. Green is allowed only inside src/pages/deck/** and src/pages/Deck.tsx.
// Usage: node scripts/check-style.mjs [root]   (root defaults to ./src)
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";

const root = process.argv[2] ?? "src";

function excluded(rel) {
  const p = rel.split(sep).join("/");
  return p.startsWith("pages/deck/") || p === "pages/Deck.tsx" || p.endsWith(".test.ts") || p.endsWith(".test.tsx");
}

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) yield* files(full);
    else if (/\.(css|tsx|ts)$/.test(name)) yield full;
  }
}

const isZero = (v) => v.trim().split(/\s+/).every((t) => /^0(px|%|rem|em)?$/.test(t));

const RULES = [
  { re: /border-radius\s*:\s*([^;}]+)/g, bad: (m) => !isZero(m[1]), why: (m) => `border-radius ${m[1].trim()} (radius is 0)` },
  { re: /box-shadow\s*:\s*([^;}]+)/g, bad: (m) => m[1].trim() !== "none", why: () => "box-shadow (no shadows)" },
  { re: /gradient\(/g, bad: () => true, why: () => "gradient() (no gradients; use var(--hatch) or a flat token)" },
  // The JSX, canvas and Plotly forms: <linearGradient>, <radialGradient>, ctx.createLinearGradient(),
  // createRadialGradient(), createConicGradient(), and Plotly's fillgradient.
  { re: /(linear|radial|conic)Gradient|fillgradient/gi, bad: () => true, why: (m) => `${m[0]} (no gradients; hatch the area or use a flat token)` },
  { re: /Fraunces|Inter Variable/g, bad: () => true, why: (m) => `${m[0]} (faces are Archivo, IBM Plex Sans, IBM Plex Mono)` },
  { re: /--gain-soft|--crt-green/g, bad: () => true, why: (m) => `${m[0]} (no green outside the Deck)` },
];

const hits = [];
for (const file of files(root)) {
  const rel = relative(root, file);
  if (excluded(rel)) continue;
  const text = readFileSync(file, "utf8");
  for (const rule of RULES) {
    for (const m of text.matchAll(rule.re)) {
      if (!rule.bad(m)) continue;
      const line = text.slice(0, m.index).split("\n").length;
      hits.push(`${join(root, rel)}:${line}: ${rule.why(m)}`);
    }
  }
}

if (hits.length) {
  console.error(`check-style: ${hits.length} violation${hits.length > 1 ? "s" : ""}`);
  for (const h of hits) console.error("  " + h);
  process.exit(1);
}
console.log("check-style: ok");
