import { describe, expect, it } from "vitest";
import { coefLayout, MONO_ADVANCE } from "./CoefChart";

const rows = ["1/N", "MV", "MINVAR", "RP", "HRP", "HERC", "NCO"].map((term) => ({ term }));
const label = "p 0.001 · HOLM 0.007";

describe("coefLayout", () => {
  it("reserves a right column wide enough for the longest right-edge label", () => {
    const L = coefLayout(rows, [label, "t 41.2"], 1100);
    expect(L.stacked).toBe(false);
    // the right-anchored label starts at width - its own width; the plot must end before it, with a gap
    expect(L.plotR).toBeLessThanOrEqual(1100 - label.length * MONO_ADVANCE - 8);
  });
  it("a short label keeps a short gutter", () => {
    const L = coefLayout(rows, ["t 4.1"], 1100);
    expect(1100 - L.plotR).toBeLessThan(64);
  });
  it("at phone width the label still clears the plot", () => {
    const L = coefLayout(rows, [label], 348);
    expect(L.plotR).toBeLessThanOrEqual(348 - label.length * MONO_ADVANCE - 8);
    expect(L.plotR - L.plotL).toBeGreaterThanOrEqual(120);
  });
  it("when the column would squeeze the plot, the label drops to its own line under the whisker", () => {
    const L = coefLayout(rows, [label], 260);
    expect(L.stacked).toBe(true);
    expect(L.plotR).toBe(260 - 20);
    expect(L.row).toBeGreaterThan(26);
  });
});

describe("coefLayout with a measured label width", () => {
  it("sizes the gutter from the measured width, not the glyph estimate", () => {
    // a fallback font set wider than Plex Mono: the gutter follows what was measured
    const wide = (s: string) => s.length * 8;
    const L = coefLayout(rows, [label], 1100, wide);
    expect(L.plotR).toBeLessThanOrEqual(1100 - label.length * 8 - 8);
  });
  it("a measure that cannot measure (0) falls back to the estimate", () => {
    const L = coefLayout(rows, [label], 1100, () => 0);
    expect(L.plotR).toBeLessThanOrEqual(1100 - label.length * MONO_ADVANCE - 8);
  });
});
