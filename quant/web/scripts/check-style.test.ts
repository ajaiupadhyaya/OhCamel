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
