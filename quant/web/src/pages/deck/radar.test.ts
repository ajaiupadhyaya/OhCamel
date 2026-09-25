import { describe, expect, test } from "vitest";
import { BLIP_MAX, BLIP_MIN, RIM_SHARE, bearingOf, polar, radar, rangeOf, sizeOf, type BlipIn } from "./radar";

const b = (ticker: string, pct_var: number | null, o: Partial<BlipIn> = {}): BlipIn => ({ ticker, pct_var, live_weight: 0.25, change_pct: 0.01, component_var: pct_var == null ? null : pct_var * 0.02, ...o });

describe("radar geometry", () => {
  test("bearings are fixed by book order, clockwise from north", () => {
    expect([0, 1, 2, 3].map((i) => bearingOf(i, 4))).toEqual([0, 90, 180, 270]);
    expect(bearingOf(0, 0)).toBe(0);
    const m = radar([b("A", 0.3), b("B", 0.3), b("C", 0.3), b("D", 0.3)]);
    expect(m.blips.map((x) => x.bearing)).toEqual([0, 90, 180, 270]);
  });

  test("a blip keeps its bearing when another holding's risk changes", () => {
    const one = radar([b("A", 0.5), b("B", 0.1), b("C", 0.4)]);
    const two = radar([b("A", 0.1), b("B", 0.8), b("C", 0.1)]);
    expect(two.blips.map((x) => x.bearing)).toEqual(one.blips.map((x) => x.bearing));
  });

  test("north is up and east is right, in SVG coordinates", () => {
    expect(polar(0, 1)).toEqual({ x: 0, y: -1 });
    expect(polar(90, 1)).toEqual({ x: 1, y: 0 });
    expect(polar(180, 0.5)).toEqual({ x: 0, y: 0.5 });
    expect(polar(270, 1)).toEqual({ x: -1, y: 0 });
  });

  test("range is |share of VaR| on a linear scale, the rim at RIM_SHARE", () => {
    expect(rangeOf(0)).toEqual({ range: 0, clamped: false });
    expect(rangeOf(RIM_SHARE / 2).range).toBeCloseTo(0.5, 12);
    expect(rangeOf(0.3).range).toBeCloseTo(0.3 / RIM_SHARE, 12);
    expect(rangeOf(-0.3).range).toBeCloseTo(0.3 / RIM_SHARE, 12);
  });

  test("a share past the rim is clamped to it and flagged", () => {
    expect(rangeOf(0.95)).toEqual({ range: 1, clamped: true });
    expect(rangeOf(RIM_SHARE)).toEqual({ range: 1, clamped: false });
    const m = radar([b("A", 1.2)]);
    expect(m.blips[0].range).toBe(1);
    expect(m.blips[0].clamped).toBe(true);
  });

  test("rings sit at 10 / 25 / 50 % of VaR, inside the rim", () => {
    const m = radar([]);
    expect(m.rings.map((r) => r.share)).toEqual([0.1, 0.25, 0.5]);
    m.rings.forEach((r) => expect(r.range).toBeLessThan(1));
    expect(m.rings[2].range).toBeCloseTo(0.5 / RIM_SHARE, 12);
  });

  test("size: area proportional to |live weight|, floored, capped at |w| = 1", () => {
    expect(sizeOf(1)).toBeCloseTo(BLIP_MAX, 12);
    expect(sizeOf(-1)).toBeCloseTo(BLIP_MAX, 12);
    expect(sizeOf(0.25)).toBeCloseTo(BLIP_MAX * 0.5, 12);
    expect(sizeOf(0.25) / sizeOf(1)).toBeCloseTo(Math.sqrt(0.25), 12);
    expect(sizeOf(3)).toBeCloseTo(BLIP_MAX, 12);
    expect(sizeOf(0)).toBe(BLIP_MIN);
    expect(sizeOf(null)).toBe(BLIP_MIN);
  });

  test("a negative component VaR is a hedge", () => {
    const m = radar([b("TLT", -0.08, { component_var: -1234 }), b("SPY", 0.7, { component_var: 5000 })]);
    expect(m.blips.find((x) => x.ticker === "TLT")!.hedge).toBe(true);
    expect(m.blips.find((x) => x.ticker === "SPY")!.hedge).toBe(false);
    expect(m.summary).toContain("Hedging (negative component VaR): TLT.");
  });

  test("tone follows today's move; zero or unknown is flat", () => {
    const m = radar([b("A", 0.1, { change_pct: 0.004 }), b("B", 0.1, { change_pct: -0.02 }), b("C", 0.1, { change_pct: 0 }), b("D", 0.1, { change_pct: null })]);
    expect(m.blips.map((x) => x.tone)).toEqual(["gain", "loss", "flat", "flat"]);
  });

  test("a holding with no VaR share is listed, never drawn at the centre", () => {
    const m = radar([b("A", 0.4), b("B", null), b("C", 0.6)]);
    expect(m.blips.map((x) => x.ticker)).toEqual(["A", "C"]);
    expect(m.unplaced).toEqual(["B"]);
    // the others keep their book-order bearings (3 slots, B's is empty)
    expect(m.blips.map((x) => x.bearing)).toEqual([0, 240]);
    expect(m.summary).toContain("No VaR share for: B.");
  });

  test("the summary names the largest share of risk", () => {
    const m = radar([b("SPY", 0.62), b("QQQ", 0.25), b("GLD", 0.03, { change_pct: -0.01 })]);
    expect(m.summary).toBe("3 of 3 holdings placed by share of value at risk. Largest: SPY at 62% of VaR. 2 up and 1 down today.");
    expect(radar(null).summary).toBe("No holdings on the radar.");
  });
});
