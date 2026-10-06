import { describe, expect, it } from "vitest";
import { freshnessRows, lastPair, slope2s10s } from "./data";

const frame = {
  index: ["2026-05-28", "2026-05-29", "2026-06-01"],
  columns: ["DGS2", "DGS10"],
  data: { DGS2: [4.0, 4.1, null], DGS10: [4.4, 4.45, 4.47] },
};

describe("slope2s10s", () => {
  it("uses the latest date where both legs print, in bp", () => {
    expect(slope2s10s(frame)).toEqual({ bp: 35, date: "2026-05-29" });
  });
  it("negative is inverted", () => {
    expect(slope2s10s({ ...frame, data: { DGS2: [4.6], DGS10: [4.2] }, index: ["d"] })?.bp).toBe(-40);
  });
  it("no common print → null", () => {
    expect(slope2s10s({ index: ["a"], columns: [], data: { DGS2: [null], DGS10: [4] } })).toBeNull();
  });
});

describe("lastPair", () => {
  it("returns the last two printed values of a column", () => {
    expect(lastPair(frame, "DGS10")).toEqual({ last: 4.47, prev: 4.45, date: "2026-06-01" });
    expect(lastPair(frame, "DGS2")).toEqual({ last: 4.1, prev: 4.0, date: "2026-05-29" });
  });
  it("missing column → null", () => expect(lastPair(frame, "DGS30")).toBeNull());
});

describe("freshnessRows (GET /api/warehouse/freshness)", () => {
  it("reads a list of datasets", () => {
    expect(freshnessRows({ datasets: [{ dataset: "bars_daily", data_asof: "2026-10-02", status: "ok" }] })).toEqual([{ dataset: "bars_daily", data_asof: "2026-10-02", status: "ok", max_age_s: undefined }]);
  });
  it("reads a dataset → data_asof record", () => {
    expect(freshnessRows({ fred: "2026-10-02", bars_daily: null }).map((r) => [r.dataset, r.data_asof])).toEqual([
      ["bars_daily", null],
      ["fred", "2026-10-02"],
    ]);
  });
  it("tolerates garbage", () => {
    expect(freshnessRows(null)).toEqual([]);
    expect(freshnessRows({ datasets: [{ nope: 1 }] })).toEqual([]);
  });
});
