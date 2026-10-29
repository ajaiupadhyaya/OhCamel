import { describe, expect, it } from "vitest";
import { isMachineSource, isThree, staticClosure, threeManifestFailures, threeSourceFailures, type Manifest, type ManifestChunk as Chunk } from "./bundle-rules.mjs";

const THREE: Chunk = { file: "assets/three-a1.js", name: "three" };
const manifest = (extra: Manifest = {}, entry: Partial<Chunk> = {}): Manifest => ({
  "index.html": { file: "assets/index-a1.js", src: "index.html", isEntry: true, imports: ["_vendor.js"], ...entry },
  "_vendor.js": { file: "assets/vendor-a1.js", name: "vendor" },
  "_three.js": THREE,
  ...extra,
});
const failures = (m: Manifest): string[] => threeManifestFailures(m, "index.html");

describe("isMachineSource: where three.js may live", () => {
  it("is src/pages/machine/** and src/pages/Machine.tsx and nothing near them", () => {
    for (const ok of ["src/pages/machine/scene.ts", "src/pages/machine/deep/x.ts", "src/pages/Machine.tsx"]) expect(isMachineSource(ok), ok).toBe(true);
    for (const no of ["src/pages/machine-notes/x.ts", "src/pages/MachineList.tsx", "src/pages/Front.tsx", "src/pages/machine.ts", "src/lib/machine/x.ts", "", undefined])
      expect(isMachineSource(no), String(no)).toBe(false);
  });
});

describe("isThree: finding the three.js chunk", () => {
  it("matches the manualChunks chunk by name and a module by its node_modules path", () => {
    expect(isThree("_three.js", THREE)).toBe(true);
    expect(isThree("node_modules/three/build/three.module.js", { file: "assets/x.js" })).toBe(true);
    expect(isThree("node_modules/three/examples/jsm/controls/OrbitControls.js", { file: "assets/x.js" })).toBe(true);
  });
  it("does not match modules merely named after it", () => {
    expect(isThree("src/pages/machine/three.ts", { file: "assets/three-scene.js", name: "three-scene" })).toBe(false);
    expect(isThree("node_modules/three-stdlib/x.js", { file: "assets/x.js" })).toBe(false);
  });
});

describe("staticClosure", () => {
  it("follows static imports only", () => {
    const m = manifest({ "_a.js": { file: "a.js", imports: ["_three.js"] } }, { imports: ["_a.js"], dynamicImports: ["_vendor.js"] });
    // _vendor.js is only a dynamic import of the entry here, so it is not in the closure
    expect([...staticClosure(m, "index.html")].sort()).toEqual(["_a.js", "_three.js", "index.html"]);
    expect([...staticClosure(manifest(), "index.html")].sort()).toEqual(["_vendor.js", "index.html"]);
  });
});

describe("threeManifestFailures", () => {
  it("passes a build with no three chunk", () => {
    expect(failures({ "index.html": manifest()["index.html"], "_vendor.js": manifest()["_vendor.js"] })).toEqual([]);
  });
  it("passes a machine-only build: the scene or the Machine page imports it, the entry does not", () => {
    expect(failures(manifest({ s: { file: "assets/scene-a1.js", src: "src/pages/machine/scene.ts", dynamicImports: ["_three.js"] } }))).toEqual([]);
    expect(failures(manifest({ s: { file: "assets/Machine-a1.js", src: "src/pages/Machine.tsx", imports: ["_three.js"] } }))).toEqual([]);
  });
  it("fails when the entry reaches three through static imports, directly or through a shared chunk", () => {
    // the entry is itself a chunk outside the machine that imports three, so it is named too
    expect(failures(manifest({}, { imports: ["_vendor.js", "_three.js"] }))).toEqual([
      "three (assets/three-a1.js) is statically reachable from the entry",
      "assets/index-a1.js (index.html) imports three; only src/pages/machine/** and src/pages/Machine.tsx may",
    ]);
    expect(failures(manifest({ "_a.js": { file: "assets/a.js", imports: ["_three.js"] } }, { imports: ["_a.js"] })).join("\n")).toContain("statically reachable from the entry");
  });
  it("fails when a non-machine chunk imports three, statically or dynamically, naming the chunk and its source", () => {
    for (const via of ["imports", "dynamicImports"] as const) {
      const out = failures(manifest({ f: { file: "assets/Front-a1.js", src: "src/pages/Front.tsx", [via]: ["_three.js"] } }));
      expect(out, via).toEqual(["assets/Front-a1.js (src/pages/Front.tsx) imports three; only src/pages/machine/** and src/pages/Machine.tsx may"]);
    }
  });
  it("fails a shared chunk with no source, and look-alike paths", () => {
    expect(failures(manifest({ shared: { file: "assets/shared-a1.js", dynamicImports: ["_three.js"] } }))).toEqual(["assets/shared-a1.js (shared) imports three; only src/pages/machine/** and src/pages/Machine.tsx may"]);
    expect(failures(manifest({ l: { file: "assets/l.js", src: "src/pages/machine-notes/x.ts", dynamicImports: ["_three.js"] } }))).toHaveLength(1);
  });
  it("finds three by module path when the chunk carries no name", () => {
    const m = manifest({ "node_modules/three/build/three.module.js": { file: "assets/t.js" }, f: { file: "assets/f.js", src: "src/pages/Front.tsx", imports: ["node_modules/three/build/three.module.js"] } });
    delete m["_three.js"];
    expect(failures(m)).toHaveLength(1);
  });
  it("reports every offender", () => {
    const m = manifest({
      a: { file: "assets/a.js", src: "src/pages/Front.tsx", dynamicImports: ["_three.js"] },
      b: { file: "assets/b.js", src: "src/pages/Risk.tsx", dynamicImports: ["_three.js"] },
    });
    expect(failures(m)).toHaveLength(2);
  });
});

describe("threeSourceFailures", () => {
  const imports = ['import * as THREE from "three";', "import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';", 'const t = await import("three");', 'import "three";', 'export type { Color } from "three/src/math/Color.js";'];
  it("fails each way of importing three outside the machine", () => {
    for (const text of imports) expect(threeSourceFailures("src/pages/Front.tsx", text), text).toEqual(["src/pages/Front.tsx imports three; only src/pages/machine/** and src/pages/Machine.tsx may"]);
  });
  it("passes the same text inside the machine", () => {
    for (const text of imports) for (const rel of ["src/pages/machine/scene.ts", "src/pages/Machine.tsx"]) expect(threeSourceFailures(rel, text), `${rel} ${text}`).toEqual([]);
  });
  it("passes text that only says three", () => {
    for (const text of ['const n = "three";', "// three.js is confined", 'import x from "three-stdlib";', 'import { three } from "./three";', 'const label = "one of three";'])
      expect(threeSourceFailures("src/pages/Front.tsx", text), text).toEqual([]);
  });
});
