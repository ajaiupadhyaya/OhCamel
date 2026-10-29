import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import type { Manifest, ManifestChunk as Chunk } from "./bundle-rules.mjs";

// The real script, run against a fake dist/ and src/ (like check-style.test.ts): proves the
// three.js rule is wired into the build, not only correct in isolation (bundle-rules.test.ts).
const script = new URL("./check-bundle.mjs", import.meta.url).pathname;
const SURFACE = "src/pages/volatility/SurfaceView.tsx";
const dirs: string[] = [];
afterEach(() => dirs.splice(0).forEach((d) => rmSync(d, { recursive: true, force: true })));

// What the real build emits today: an entry, the vendor chunk, Plotly, and the surface view alone importing it.
function baseManifest(): Manifest {
  return {
    "index.html": { file: "assets/index-a1.js", src: "index.html", isEntry: true, imports: ["_vendor-a1.js"], dynamicImports: [SURFACE] },
    "_vendor-a1.js": { file: "assets/vendor-a1.js", name: "vendor" },
    "_plotly-a1.js": { file: "assets/plotly-a1.js", name: "plotly" },
    [SURFACE]: { file: "assets/SurfaceView-a1.js", src: SURFACE, imports: ["_plotly-a1.js"] },
  };
}

function run(manifest: Manifest, sources: Record<string, string> = { "App.tsx": "export const a = 1;\n" }) {
  const root = mkdtempSync(join(tmpdir(), "check-bundle-"));
  dirs.push(root);
  const put = (path: string, text: string) => {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, text);
  };
  put(join(root, "dist", ".vite", "manifest.json"), JSON.stringify(manifest));
  for (const c of Object.values(manifest)) put(join(root, "dist", c.file), `// ${c.file}\n`);
  for (const [name, text] of Object.entries(sources)) put(join(root, "src", name), text);
  return spawnSync(process.execPath, [script, join(root, "dist"), join(root, "src")], { encoding: "utf8" });
}

describe("check-bundle: the fixture", () => {
  it("passes today's build, which has no three chunk", () => {
    const r = run(baseManifest());
    expect(r.stderr).toBe("");
    expect(r.status).toBe(0);
    expect(r.stdout).toContain("check-bundle: ok");
  });
});

describe("check-bundle confines three.js to the machine", () => {
  const withThree = (importer: Chunk, via: "imports" | "dynamicImports" = "dynamicImports") => {
    const m = baseManifest();
    m["_three-a1.js"] = { file: "assets/three-a1.js", name: "three" };
    m["importer"] = { ...importer, [via]: ["_three-a1.js"] };
    m["index.html"].dynamicImports!.push("importer"); // the page is lazy: reached from the entry, not part of it
    return m;
  };
  // The script lists each importer as "static" or "dynamic": asserting it proves which path ran.
  it("passes when only the machine's scene imports it, dynamically", () => {
    const r = run(withThree({ file: "assets/scene-a1.js", src: "src/pages/machine/scene.ts" }, "dynamicImports"));
    expect(r.stderr).toBe("");
    expect(r.status).toBe(0);
    expect(r.stdout).toContain("assets/scene-a1.js (src/pages/machine/scene.ts) dynamic");
  });
  it("passes when the Machine page itself imports it, statically", () => {
    const r = run(withThree({ file: "assets/Machine-a1.js", src: "src/pages/Machine.tsx" }, "imports"));
    expect(r.stderr).toBe("");
    expect(r.status).toBe(0);
    expect(r.stdout).toContain("assets/Machine-a1.js (src/pages/Machine.tsx) static");
  });
  it("fails when a chunk outside the machine imports it", () => {
    const r = run(withThree({ file: "assets/Front-a1.js", src: "src/pages/Front.tsx" }));
    expect(r.status).toBe(1);
    expect(r.stderr).toMatch(/Front-a1\.js.*three/);
  });
  it("fails when the entry reaches it statically", () => {
    const m = baseManifest();
    m["_three-a1.js"] = { file: "assets/three-a1.js", name: "three" };
    m["index.html"].imports!.push("_three-a1.js");
    const r = run(m);
    expect(r.status).toBe(1);
    expect(r.stderr).toMatch(/three.*statically reachable from the entry/);
  });
  it("fails when a source file outside the machine imports it", () => {
    const r = run(baseManifest(), { "App.tsx": 'import * as THREE from "three";\n' });
    expect(r.status).toBe(1);
    expect(r.stderr).toMatch(/src\/App\.tsx.*three/);
  });
  it("passes the same import inside src/pages/machine/", () => {
    expect(run(baseManifest(), { "pages/machine/scene.ts": 'import * as THREE from "three";\n' }).status).toBe(0);
  });
});
