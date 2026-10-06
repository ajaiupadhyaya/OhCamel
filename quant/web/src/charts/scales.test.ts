import { describe, expect, it } from "vitest";
import { alignSeries, endLabels, formatTick, formatValue, niceTicks } from "./scales";

describe("niceTicks", () => {
  it("1-2-5 steps covering the range", () => {
    expect(niceTicks(0, 9.3, 5)).toEqual([0, 2, 4, 6, 8, 10]);
    expect(niceTicks(-0.031, 0.044, 4)).toEqual([-0.04, -0.02, 0, 0.02, 0.04, 0.06]);
  });
  it("a flat series still gets ticks", () => {
    expect(niceTicks(5, 5, 4).length).toBeGreaterThanOrEqual(2);
  });
});

describe("endLabels", () => {
  it("separates labels that would overlap by at least the min gap", () => {
    const out = endLabels([{ y: 100, text: "A" }, { y: 101, text: "B" }, { y: 300, text: "C" }], 12);
    const ys = out.map((o) => o.y).sort((a, b) => a - b);
    expect(ys[1] - ys[0]).toBeGreaterThanOrEqual(12);
    expect(out.find((o) => o.text === "C")!.y).toBe(300);
  });
});

describe("alignSeries", () => {
  it("joins series on the union of their dates, missing points as null", () => {
    const out = alignSeries([
      { x: ["2026-01-02", "2026-01-05"], y: [1, 2] },
      { x: ["2026-01-05", "2026-01-06"], y: [3, 4] },
    ]);
    expect(out.t).toEqual([Date.UTC(2026, 0, 2) / 1000, Date.UTC(2026, 0, 5) / 1000, Date.UTC(2026, 0, 6) / 1000]);
    expect(out.ys).toEqual([[1, 2, null], [null, 3, 4]]);
  });
  it("drops unparseable dates and non-finite values instead of inventing them", () => {
    const out = alignSeries([{ x: ["2026-01-02", "soon", "2026-01-03"], y: [1, 5, Number.NaN] }]);
    expect(out.t).toEqual([Date.UTC(2026, 0, 2) / 1000, Date.UTC(2026, 0, 3) / 1000]);
    expect(out.ys).toEqual([[1, null]]);
  });
});

describe("formatValue / formatTick", () => {
  it("uses a true minus, an explicit plus when signed, and a dash for missing", () => {
    expect(formatValue(-0.0123, "pct", 2)).toBe("−1.23%");
    expect(formatValue(0.0123, "pct", 1, true)).toBe("+1.2%");
    expect(formatValue(1234.5, "usd", 2)).toBe("$1,234.50");
    expect(formatValue(null, "num", 2)).toBe("—");
    expect(formatValue(12.4, "bps", 0)).toBe("12 bp");
  });
  it("tick decimals follow the tick step", () => {
    expect(formatTick(0.02, "pct", 0.02)).toBe("2%");
    expect(formatTick(0.005, "pct", 0.005)).toBe("0.5%");
    expect(formatTick(1500, "usd", 500)).toBe("$1,500");
    expect(formatTick(-1.5, "num", 0.5)).toBe("−1.5");
  });
});
