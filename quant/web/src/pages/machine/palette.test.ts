import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readPalette } from "./palette";

const tokens = readFileSync(new URL("../../styles/tokens.css", import.meta.url), "utf8");
const TOKENS = Object.fromEntries([...tokens.matchAll(/(--mx-[a-z]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1], m[2]]));

afterEach(() => vi.unstubAllGlobals());

/** getComputedStyle as a browser answers it: the token's raw text, a leading space and all. */
const stub = (values: Record<string, string>): void => {
  vi.stubGlobal("getComputedStyle", () => ({ getPropertyValue: (name: string) => ` ${values[name] ?? ""}` }));
};

describe("readPalette", () => {
  it("reads every colour from its token", () => {
    stub(TOKENS);
    const p = readPalette({} as Element);
    expect(Object.keys(p).sort()).toEqual(["bright", "dim", "feeds", "fail", "ground", "ink", "line", "reads", "structure"].sort());
    for (const [key, color] of Object.entries(p)) expect(`#${color.getHexString("srgb-linear")}`).toBe(TOKENS[`--mx-${key}`].toLowerCase());
  });

  it("keeps a token's numbers as they are: the scene works in display space, so #808080 must not become 0.216", () => {
    stub({ ...TOKENS, "--mx-reads": "#808080" });
    const c = readPalette({} as Element).reads;
    expect(c.r).toBeCloseTo(128 / 255, 6);
    expect(c.g).toBeCloseTo(128 / 255, 6);
    expect(c.b).toBeCloseTo(128 / 255, 6);
  });

  it("has a token for every colour it reads", () => {
    for (const key of ["ground", "ink", "bright", "dim", "line", "structure", "reads", "feeds", "fail"]) expect(TOKENS[`--mx-${key}`]).toMatch(/^#/);
  });
});
