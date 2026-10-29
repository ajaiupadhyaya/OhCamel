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
    // The retired faces, spelt in parts so the GP2 grep stays empty outside lib/format.ts.
    expect(css).not.toMatch(new RegExp(["Fraun" + "ces", "Inter " + "Variable"].join("|")));
  });
});

// The machine's palette: always dark, theme-independent, so it sits in the plain :root block.
const MX = {
  "--mx-ground": "#000000",
  "--mx-ink": "#d4d4d4",
  "--mx-bright": "#f2f2f2",
  "--mx-dim": "#8a8a8a",
  "--mx-line": "#252525",
  "--mx-structure": "#2c3a42",
  "--mx-reads": "#ffb547",
  "--mx-feeds": "#38c8ff",
  "--mx-fail": "#ff4f9a",
} as const;

function hueSat(hex: string): { h: number; s: number } {
  const n = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((k) => parseInt(n.slice(k, k + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (d === 0) return { h: 0, s: 0 };
  const s = d / (1 - Math.abs(max + min - 1));
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: (h * 60 + 360) % 360, s };
}

describe("the machine's palette", () => {
  const root = block(":root");
  const carbon = block(':root[data-theme="carbon"]');
  it("defines each --mx-* colour at its exact value", () => {
    for (const [name, hex] of Object.entries(MX)) expect(root[name], name).toBe(hex);
  });
  it("is theme-independent: no theme block redefines an --mx-* token", () => {
    for (const name of Object.keys(carbon)) expect(name, name).not.toMatch(/^--mx-/);
    // nine colours and two faces, each declared exactly once (so the media-query block cannot override them)
    expect(css.match(/^\s*--mx-[a-z-]+\s*:/gm)?.length).toBe(Object.keys(MX).length + 2);
  });
  it("ink reaches 7:1 and dim 4.5:1 on the ground", () => {
    expect(contrast(root["--mx-ink"], root["--mx-ground"])).toBeGreaterThanOrEqual(7);
    expect(contrast(root["--mx-dim"], root["--mx-ground"])).toBeGreaterThanOrEqual(4.5);
  });
  it("has no green: no --mx-* colour has a hue of 90-150 degrees at saturation above 0.2", () => {
    const colours = Object.entries(root).filter(([name, value]) => name.startsWith("--mx-") && /^#[0-9A-Fa-f]{6}$/.test(value));
    expect(colours.length).toBe(Object.keys(MX).length);
    for (const [name, value] of colours) {
      const { h, s } = hueSat(value);
      expect(h >= 90 && h <= 150 && s > 0.2, `${name} ${value} is green`).toBe(false);
    }
  });
  it("sets the machine's faces: the system stack first, the self-hosted Plex faces as fallbacks", () => {
    expect(root["--mx-font-ui"]).toBe('system-ui, -apple-system, "SF Pro Text", "IBM Plex Sans", sans-serif');
    expect(root["--mx-font-mono"]).toBe('ui-monospace, "SF Mono", "IBM Plex Mono", monospace');
  });
});
