import { describe, expect, it } from "vitest";
import { logXRange, xyData, xyWhiskers } from "./XYChart";

describe("xyData", () => {
  it("drops unparseable x, nulls non-finite y, and on a log axis non-positive y", () => {
    const d = xyData([1, "x", 3], [{ name: "a", y: [0.5, 2, NaN] }, { name: "b", y: [-1, 1, 2] }], false, true);
    expect(d[0]).toEqual([1, 3]);
    expect(d[1]).toEqual([0.5, null]);
    expect(d[2]).toEqual([null, 2]);
  });
  it("a time axis is unix seconds", () => {
    expect(xyData(["2026-01-02"], [{ name: "a", y: [1] }], true, false)[0]).toEqual([Date.UTC(2026, 0, 2) / 1000]);
  });
});

describe("xyWhiskers (bid-ask whiskers on point series)", () => {
  it("keeps lo/hi aligned with the x values xyData keeps, null where missing", () => {
    const w = xyWhiskers([1, "x", 3, 4], [{ name: "a", y: [1, 2, 3, 4], mode: "points", lo: [0.9, 1.9, null, 3.8], hi: [1.2, 2.2, 3.1, NaN] }, { name: "b", y: [1, 1, 1, 1] }]);
    expect(w[0]).toEqual({ lo: [0.9, null, 3.8], hi: [1.2, 3.1, null] });
    expect(w[1]).toBeNull();
  });
});

describe("logXRange", () => {
  it("pads a log x range on the left only (line ends meet the label gutter), never below zero", () => {
    const [lo, hi] = logXRange(10, 252);
    expect(lo).toBeGreaterThan(0);
    expect(lo).toBeLessThan(10);
    expect(hi).toBe(252);
    const [a, b] = logXRange(5, 5);
    expect(a).toBeLessThan(5);
    expect(b).toBeGreaterThan(5);
  });
});
