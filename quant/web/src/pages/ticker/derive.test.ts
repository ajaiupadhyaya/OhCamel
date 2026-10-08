import { describe, expect, it } from "vitest";
import { drawdown, monthlyTable, rangeStart, sliceWindow } from "./derive";

describe("rangeStart", () => {
  it("counts calendar months back from the last session", () => {
    expect(rangeStart("1M", "2026-06-05")).toBe("2026-05-05");
    expect(rangeStart("1Y", "2026-06-05")).toBe("2025-06-05");
    expect(rangeStart("5Y", "2026-06-05")).toBe("2021-06-05");
  });
  it("YTD starts after the prior year end; MAX has no start", () => {
    expect(rangeStart("YTD", "2026-06-05")).toBe("2025-12-31");
    expect(rangeStart("MAX", "2026-06-05")).toBeNull();
  });
});

describe("sliceWindow", () => {
  const frame = {
    index: ["2025-12-30", "2025-12-31", "2026-01-02", "2026-01-05"],
    columns: ["adj_close", "close", "volume"],
    data: { adj_close: [1, 2, 3, 4], close: [1, 2, 3, 4], volume: [10, 20, 30, 40] },
  };
  it("keeps sessions strictly after the range start", () => {
    const v = sliceWindow(frame as never, "YTD")!;
    expect(v.dates).toEqual(["2026-01-02", "2026-01-05"]);
    expect(v.adj).toEqual([3, 4]);
    expect(v.volume).toEqual([30, 40]);
  });
  it("is null for an empty frame", () => {
    expect(sliceWindow({ index: [], columns: [], data: {} } as never, "1Y")).toBeNull();
  });
});

describe("drawdown", () => {
  it("is the fall from the running peak, null where the level is missing", () => {
    expect(drawdown([100, 110, null, 99, 121])).toEqual([0, 0, null, 99 / 110 - 1, 0]);
  });
});

describe("monthlyTable", () => {
  it("compounds calendar-month returns into a year figure and flags partial years", () => {
    const dates = ["2025-11-28", "2025-12-31", "2026-01-30", "2026-02-27"];
    const t = monthlyTable(dates, [100, 110, 99, 108.9]);
    expect(t.map((r) => r.year)).toEqual([2026, 2025]);
    const y26 = t[0];
    expect(y26.months[0]).toBeCloseTo(-0.1, 12);
    expect(y26.months[1]).toBeCloseTo(0.1, 12);
    expect(y26.months.slice(2).every((m) => m === null)).toBe(true);
    expect(y26.year_ret).toBeCloseTo(0.9 * 1.1 - 1, 12);
    expect(y26.n).toBe(2);
    expect(t[1].months[11]).toBeCloseTo(0.1, 12);
    expect(t[1].n).toBe(1);
  });
});
