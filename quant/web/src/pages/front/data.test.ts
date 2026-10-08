import { describe, expect, it } from "vitest";
import { freshnessRows, lampOf, lastPair, slope2s10s, stateLabel, vixFigure } from "./data";

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
  it("reads the served shape: {now, datasets: {name: {data_asof, stale, last_status, keys_failed}}}", () => {
    const body = {
      now: "2026-10-08T00:49:55Z",
      datasets: {
        fred: { data_asof: "2026-06-01", last_run_at: "2026-06-09T17:53:15", last_status: "ok", keys: 3, keys_behind: 0, keys_failed: 0, stale: true, rule: "age" },
        bars_daily: { data_asof: "2026-10-06", last_status: "ok", keys: 9, keys_behind: 1, keys_failed: 2, stale: false, rule: "session" },
      },
      provenance: [],
      notes: [],
    };
    expect(freshnessRows(body)).toEqual([
      { dataset: "bars_daily", data_asof: "2026-10-06", status: "ok", stale: false, keys: 9, keys_behind: 1, keys_failed: 2 },
      { dataset: "fred", data_asof: "2026-06-01", status: "ok", stale: true, keys: 3, keys_behind: 0, keys_failed: 0 },
    ]);
  });
  it("lampOf: a failed run or failed keys is a fault; the API's stale flag wins over age", () => {
    const now = new Date("2026-10-07T12:00:00Z");
    expect(lampOf({ dataset: "a", data_asof: "2026-10-06", status: "failed" }, now)).toBe("fault");
    expect(lampOf({ dataset: "a", data_asof: "2026-10-06", status: "ok", keys_failed: 1 }, now)).toBe("fault");
    expect(lampOf({ dataset: "a", data_asof: "2026-10-06", status: "ok", stale: true }, now)).toBe("stale");
    expect(lampOf({ dataset: "a", data_asof: "2026-01-01", status: "ok", stale: false }, now)).toBe("ok");
    expect(lampOf({ dataset: "a", data_asof: null }, now)).toBe("idle");
  });
  it("a dataset never ingested (0 keys, no data date) is NOT YET RUN, not STALE", () => {
    const now = new Date("2026-10-07T12:00:00Z");
    const never = { dataset: "sec_facts", data_asof: null, keys: 0, stale: true };
    expect(lampOf(never, now)).toBe("idle");
    expect(stateLabel(lampOf(never, now))).toBe("NOT YET RUN");
    expect(lampOf({ ...never, status: "failed" }, now)).toBe("fault");
    expect(lampOf({ dataset: "a", data_asof: "2026-01-01", keys: 3, stale: true }, now)).toBe("stale");
    expect(stateLabel("stale")).toBe("STALE");
  });
  it("tolerates garbage", () => {
    expect(freshnessRows(null)).toEqual([]);
    expect(freshnessRows({ datasets: [{ nope: 1 }] })).toEqual([]);
  });
});

describe("vixFigure (^VIX quote, else FRED VIXCLS)", () => {
  const fred = { index: ["2026-05-28", "2026-05-29", "2026-06-01"], columns: ["VIXCLS"], data: { VIXCLS: [15.74, 16.0, null] } };
  it("the quote when the overview has ^VIX", () => {
    expect(vixFigure({ ticker: "^VIX", last: 17.2, ret_1d: 0.05, as_of: "2026-06-01", error: null } as never, fred)).toEqual({ value: 17.2, ret1d: 0.05, asOf: "2026-06-01", source: "quote" });
  });
  it("FRED VIXCLS when the quote source lacks ^VIX (errored row or no row)", () => {
    const want = { value: 16.0, ret1d: 16.0 / 15.74 - 1, asOf: "2026-05-29", source: "fred" };
    expect(vixFigure({ ticker: "^VIX", error: "no fixture" } as never, fred)).toEqual(want);
    expect(vixFigure(undefined, fred)).toEqual(want);
  });
  it("neither → nothing, never a guess", () => {
    expect(vixFigure(undefined, undefined)).toEqual({ value: null, ret1d: null, asOf: null, source: "none" });
    expect(vixFigure(undefined, { index: [], columns: [], data: {} })).toEqual({ value: null, ret1d: null, asOf: null, source: "none" });
  });
});
