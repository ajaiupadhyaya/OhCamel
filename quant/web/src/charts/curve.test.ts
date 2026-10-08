import { describe, expect, it } from "vitest";
import { curveTable, tenorSplits } from "./curve";

describe("curveTable", () => {
  it("aligns curves on the union of their tenors, null where a curve has no point", () => {
    const t = curveTable([
      { key: "now", label: "NOW", tenors: [1, 2, 10], yields: [4.1, 4.0, 4.4] },
      { key: "1Y", label: "1Y", tenors: [2, 10, 30], yields: [4.5, 4.2, 4.6] },
    ]);
    expect(t.x).toEqual([1, 2, 10, 30]);
    expect(t.ys).toEqual([
      [4.1, 4.0, 4.4, null],
      [null, 4.5, 4.2, 4.6],
    ]);
  });
  it("drops non-finite and non-positive tenors (the x axis is log)", () => {
    const t = curveTable([{ key: "now", label: "NOW", tenors: [0, 1, Number.NaN, 2], yields: [1, 2, 3, Number.NaN] }]);
    expect(t.x).toEqual([1, 2]);
    expect(t.ys).toEqual([[2, null]]);
  });
});

describe("tenorSplits", () => {
  it("keeps the standard tenors inside the range", () => expect(tenorSplits(1 / 12, 30)).toEqual([0.25, 1, 2, 5, 10, 30]));
  it("narrow range", () => expect(tenorSplits(1, 10)).toEqual([1, 2, 5, 10]));
});

import { fmtTenor } from "./curve";
describe("fmtTenor", () => {
  it("months under a year, years above", () => {
    expect([1 / 12, 0.25, 0.5, 1, 2, 7, 30].map(fmtTenor)).toEqual(["1M", "3M", "6M", "1Y", "2Y", "7Y", "30Y"]);
  });
  it("fractional years keep two decimals at most", () => expect(fmtTenor(1.5)).toBe("1.5Y"));
});
