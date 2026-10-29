// The bundle guard's rules as pure functions of a Vite manifest and of source text, so
// bundle-rules.test.ts can feed them fakes. scripts/check-bundle.mjs reads the real dist/ and src/
// and calls these; it adds the entry-size limit and the Plotly rules.

// The only places three.js may be imported from: the machine (the front page's 3-D instrument),
// which the router loads lazily, so three.js stays out of the entry chunk and every other page.
export const MACHINE_DIR = "src/pages/machine/";
export const MACHINE_PAGE = "src/pages/Machine.tsx";
const WHERE = `${MACHINE_DIR}** and ${MACHINE_PAGE}`;

/** Whether a manifest `src` (or a repo-relative source path) is part of the machine. */
export const isMachineSource = (src) => typeof src === "string" && (src.startsWith(MACHINE_DIR) || src === MACHINE_PAGE);

/** The three.js chunk itself (vite.config.ts manualChunks "three"), or a module under node_modules/three/. */
export const isThree = (k, c) => c.name === "three" || /(^|\/)node_modules\/three\//.test(k);

const importsOf = (c) => [...(c.imports ?? []), ...(c.dynamicImports ?? [])];

/** The manifest keys reachable from `entryKey` through static imports (dynamic imports are lazy and do not count). */
export function staticClosure(manifest, entryKey) {
  const seen = new Set();
  const stack = [entryKey];
  while (stack.length) {
    const k = stack.pop();
    if (seen.has(k) || !manifest[k]) continue;
    seen.add(k);
    stack.push(...(manifest[k].imports ?? []));
  }
  return seen;
}

/**
 * Failures of the three.js rule on a manifest: three.js statically reachable from the entry,
 * and any chunk outside the machine that imports it (statically or dynamically; a shared chunk
 * with no `src` counts as outside).
 */
export function threeManifestFailures(manifest, entryKey) {
  const failures = [];
  const threeKeys = Object.entries(manifest).filter(([k, c]) => isThree(k, c)).map(([k]) => k);
  const closure = staticClosure(manifest, entryKey);
  for (const k of threeKeys) if (closure.has(k)) failures.push(`three (${manifest[k].file}) is statically reachable from the entry`);
  for (const [k, c] of Object.entries(manifest)) {
    if (isThree(k, c) || !importsOf(c).some((i) => threeKeys.includes(i))) continue;
    if (isMachineSource(c.src)) continue;
    failures.push(`${c.file} (${c.src ?? k}) imports three; only ${WHERE} may`);
  }
  return failures;
}

// from "three", from "three/addons/…", import "three", import("three").
const THREE_IMPORT = /\b(?:from|import)\s*\(?\s*["']three(?:\/[^"']*)?["']/;

/** Failures of the three.js rule on one source file (`rel` is repo-relative with forward slashes, e.g. src/pages/Front.tsx). */
export function threeSourceFailures(rel, text) {
  if (isMachineSource(rel)) return [];
  return THREE_IMPORT.test(text) ? [`${rel} imports three; only ${WHERE} may`] : [];
}
