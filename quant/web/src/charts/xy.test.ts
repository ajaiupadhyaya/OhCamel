import { describe, expect, it } from "vitest";
import { xyData } from "./XYChart";

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
