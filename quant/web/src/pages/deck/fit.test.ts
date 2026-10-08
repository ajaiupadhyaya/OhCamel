import { describe, expect, it } from "vitest";
import { counterBank, fitDigits, radarGeometry, scopeGeometry } from "./fit";
import { segWidth } from "./SevenSeg";

describe("fitDigits", () => {
  it("fills the height when the width allows, keeping the cell's proportions", () => {
    const f = fitDigits("1000", 400, 40);
    expect(f.h).toBe(40);
    expect(f.w / f.h).toBeCloseTo(0.58, 5);
    expect(f.width).toBeCloseTo(segWidth("1000", f.w, f.gap), 6);
    expect(f.width).toBeLessThanOrEqual(400);
  });
  it("shrinks to the width when the text is long", () => {
    const f = fitDigits("-000933", 120, 40);
    expect(f.width).toBeLessThanOrEqual(120 + 1e-9);
    expect(f.h).toBeLessThan(40);
    expect(f.w / f.h).toBeCloseTo(0.58, 5);
  });
  it("never draws below a legible floor", () => {
    expect(fitDigits("12345678", 10, 40).h).toBeGreaterThanOrEqual(12);
  });
  it("an empty string has no width", () => {
    expect(fitDigits("", 200, 30).width).toBe(0);
  });
});

describe("counterBank", () => {
  it("lays four counters in a row when each tile keeps a usable width", () => {
    const b = counterBank(900, [7, 6, 5, 5]);
    expect(b.cols).toBe(4);
  });
  it("falls back to two columns on a phone", () => {
    expect(counterBank(340, [7, 6, 5, 5]).cols).toBe(2);
  });
  it("gives every counter the same digit size, set by the longest", () => {
    const b = counterBank(900, [7, 6, 5, 5]);
    const tile = (900 - 3 * b.gap) / 4 - b.pad * 2;
    expect(segWidth("8888888", b.seg.w, b.seg.gap)).toBeLessThanOrEqual(tile + 1e-9);
    expect(b.seg.h).toBeLessThanOrEqual(b.maxH);
  });
  it("caps the digit height on wide rows", () => {
    expect(counterBank(2400, [4, 4, 4, 4]).seg.h).toBe(counterBank(2400, [4, 4, 4, 4]).maxH);
  });
});

describe("scopeGeometry", () => {
  it("draws at console scale: the frame and readout fit inside the measured box", () => {
    const g = scopeGeometry(600, 380, "1000");
    expect(g.x0).toBeGreaterThanOrEqual(0);
    expect(g.x1).toBeLessThanOrEqual(600);
    expect(g.y0).toBeGreaterThanOrEqual(0);
    expect(g.readout.y + g.readout.h).toBeLessThanOrEqual(g.caption.y);
    expect(g.caption.y).toBeLessThanOrEqual(380);
    expect(g.y1).toBeLessThan(g.readout.y);
  });
  it("centres the vanishing point in the frame", () => {
    const g = scopeGeometry(600, 380, "1000");
    expect(g.cx).toBeCloseTo((g.x0 + g.x1) / 2, 6);
    expect(g.cy).toBeCloseTo((g.y0 + g.y1) / 2, 6);
    expect(g.hw).toBeCloseTo((g.x1 - g.x0) / 2, 6);
    expect(g.hh).toBeCloseTo((g.y1 - g.y0) / 2, 6);
  });
  it("the readout's digits fit its box", () => {
    const g = scopeGeometry(320, 220, "-000933");
    expect(g.readout.seg.width).toBeLessThanOrEqual(g.readout.w - 16);
    expect(g.readout.seg.h).toBeLessThanOrEqual(g.readout.h - 8);
  });
  it("eight perspective lines run from the frame's corners and quarter points", () => {
    const g = scopeGeometry(600, 380, "1000");
    expect(g.persp).toHaveLength(8);
    for (const [x, y] of g.persp) {
      expect(x).toBeGreaterThanOrEqual(g.x0);
      expect(x).toBeLessThanOrEqual(g.x1);
      expect([g.y0, g.y1]).toContain(y);
    }
  });
  it("keeps a minimum drawable size on a tiny box", () => {
    const g = scopeGeometry(0, 0, "1000");
    expect(g.w).toBeGreaterThan(0);
    expect(g.h).toBeGreaterThan(0);
    expect(g.y1).toBeGreaterThan(g.y0);
  });
});

describe("radarGeometry", () => {
  it("a square scope set by the smaller side", () => {
    const g = radarGeometry(500, 380);
    expect(g.size).toBe(380);
    expect(g.c).toBe(190);
    expect(g.r).toBeLessThan(190);
    expect(g.r).toBeGreaterThan(150);
  });
  it("a phone width sets the size when the height is not constrained", () => {
    expect(radarGeometry(340, 0).size).toBe(340);
  });
});
