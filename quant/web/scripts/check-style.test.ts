import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

const script = new URL("./check-style.mjs", import.meta.url).pathname;
const dirs: string[] = [];
function run(name: string, text: string) {
  const dir = mkdtempSync(join(tmpdir(), "check-style-"));
  dirs.push(dir);
  writeFileSync(join(dir, name), text);
  return spawnSync(process.execPath, [script, dir], { encoding: "utf8" });
}
afterEach(() => dirs.splice(0).forEach((d) => rmSync(d, { recursive: true, force: true })));

describe("check-style catches every gradient form", () => {
  it("passes clean source", () => expect(run("a.tsx", "const a = <path fill=\"var(--ink)\" />;").status).toBe(0));
  it("CSS gradient()", () => expect(run("a.css", ".x { background: linear-gradient(red, blue); }").status).toBe(1));
  for (const form of ["<linearGradient id=\"g\" />", "<radialGradient id=\"g\" />", "ctx.createLinearGradient(0, 0, 0, 1)", "ctx.createRadialGradient(0, 0, 1, 0, 0, 2)", "{ fill: 'tozeroy', fillgradient: { type: 'vertical' } }"]) {
    it(form, () => {
      const r = run("a.tsx", form);
      expect(r.status).toBe(1);
      expect(r.stderr).toMatch(/gradient/i);
    });
  }
});

describe("check-style catches rounded SVG corners", () => {
  for (const form of ["<rect width={4} height={4} rx={8} />", '<rect rx="4" ry="4" />', "<rect ry={r} />", "rect { rx: 3px; }", 'const svg = `<rect rx="2"/>`;']) {
    it(form, () => {
      const r = run(form.startsWith("rect {") ? "a.css" : "a.tsx", form);
      expect(r.status).toBe(1);
      expect(r.stderr).toMatch(/\b(rx|ry)\b/);
    });
  }
  for (const form of ["<rect rx={0} ry=\"0\" />", "// ?tab=x&ry=1&re=2", "const proxy = 1;"]) {
    it(`passes ${form}`, () => expect(run("a.tsx", form).status).toBe(0));
  }
});

describe("check-style rx/ry: no false positives", () => {
  it("a ternary on a variable named ry", () => expect(run("a.tsx", "const y = ok(ry) ? +ry : 10;").status).toBe(0));
});

describe("check-style holds spec 8.2 (retired page-title class, paper grain)", () => {
  for (const form of ['<h1 className="oc-page-title">x</h1>', ".oc-page-titles { display: flex; }", "body::before { content: ''; }"]) {
    it(form, () => {
      const r = run(form.startsWith("<") ? "a.tsx" : "a.css", form);
      expect(r.status).toBe(1);
      expect(r.stderr).toMatch(/oc-page-title|body::before/);
    });
  }
  it("passes the Sheet title class", () => expect(run("a.tsx", '<h1 className="oc-sheet-title">x</h1>').status).toBe(0));
});
