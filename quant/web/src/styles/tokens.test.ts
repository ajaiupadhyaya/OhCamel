import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./tokens.css", import.meta.url), "utf8");

function block(selector: string): Record<string, string> {
  const i = css.indexOf(selector + " {");
  if (i < 0) throw new Error(`missing block ${selector}`);
  const body = css.slice(css.indexOf("{", i) + 1, css.indexOf("}", i));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) out[m[1]] = m[2].trim();
  return out;
}

function lum(hex: string): number {
  const n = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((k) => parseInt(n.slice(k, k + 2), 16) / 255);
  const f = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
// Hand check: #111111 on #F4F1EA → L 0.0056 vs 0.881 → (0.931)/(0.0556) = 16.7.
function contrast(a: string, b: string): number {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const SEMANTIC = ["--paper", "--paper-2", "--ink", "--ink-2", "--ink-3", "--signal"];
const TEXT = ["--ink", "--ink-2", "--signal"];

describe("paper tape tokens", () => {
  const paper = block(":root");
  const carbon = block(':root[data-theme="carbon"]');
  it("defines every semantic token in both themes", () => {
    for (const t of SEMANTIC) {
      expect(paper[t], `${t} paper`).toMatch(/^#[0-9A-Fa-f]{6}$/);
      expect(carbon[t], `${t} carbon`).toMatch(/^#[0-9A-Fa-f]{6}$/);
    }
  });
  it("text tokens reach 4.5:1 on both grounds in both themes", () => {
    for (const theme of [paper, carbon])
      for (const t of TEXT)
        for (const g of ["--paper", "--paper-2"])
          expect(contrast(theme[t], theme[g]), `${t} on ${g}`).toBeGreaterThanOrEqual(4.5);
  });
  it("legacy text aliases (--text-3, --unknown) read at --ink-2, never --ink-3 (rules and gridlines only)", () => {
    for (const t of ["--text-3", "--unknown"]) for (const theme of [paper, carbon]) expect(theme[t] ?? paper[t], t).toBe("var(--ink-2)");
  });
  it("heat-table text reaches 4.5:1 on every sequential bucket (ink on 1-3, paper on 4-5; DataTable heatStyle)", () => {
    for (const theme of [paper, carbon])
      for (let k = 1; k <= 5; k++) expect(contrast(theme[k >= 4 ? "--paper" : "--ink"], theme[`--seq-${k}`]), `seq-${k}`).toBeGreaterThanOrEqual(4.5);
  });
  it("heat-table text reaches 4.5:1 on every diverging bucket (ink on 1-2, paper on 3)", () => {
    for (const theme of [paper, carbon])
      for (const side of ["neg", "pos"])
        for (let k = 1; k <= 3; k++) expect(contrast(theme[k === 3 ? "--paper" : "--ink"], theme[`--div-${side}-${k}`]), `div-${side}-${k}`).toBeGreaterThanOrEqual(4.5);
  });
  it("has no radius, shadow or gradient", () => {
    expect(css).not.toMatch(/gradient\(/);
    expect(paper["--radius"]).toBe("0");
    expect(paper["--shadow-1"]).toBe("none");
  });
  it("uses the paper tape faces", () => {
    expect(paper["--font-display"]).toContain("Archivo");
    expect(paper["--font-ui"]).toContain("IBM Plex Sans");
    expect(paper["--font-mono"]).toContain("IBM Plex Mono");
    expect(css).not.toMatch(/Fraunces|Inter Variable/);
  });
});
