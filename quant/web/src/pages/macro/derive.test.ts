import { describe, expect, it } from "vitest";
import { alignNumeric, argmaxStates, inversions, latestProb, nberBands, probRows, q02Holdout, regimeBands, runs, seriesStyle, tenorLabel, tenorTicks } from "./derive";

describe("tenorLabel / tenorTicks", () => {
  it("months under a year, years after, no float noise", () => {
    expect([1 / 12, 0.25, 0.75, 1, 2, 2.5, 30].map(tenorLabel)).toEqual(["1M", "3M", "9M", "1Y", "2Y", "2.5Y", "30Y"]);
    expect(tenorLabel("x")).toBe("x");
  });
  it("standard tenors inside the range", () => {
    expect(tenorTicks(0.25, 10).map((t) => t.label)).toEqual(["3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]);
  });
});

describe("alignNumeric", () => {
  it("joins curves sampled at different maturities; a point one curve lacks is null", () => {
    const a = alignNumeric([
      { x: [1, 2, 5], y: [4.1, 4.0, 3.9] },
      { x: [2, 3], y: [4.05, NaN] },
    ]);
    expect(a.x).toEqual([1, 2, 3, 5]);
    expect(a.ys).toEqual([
      [4.1, 4.0, null, 3.9],
      [null, 4.05, null, null],
    ]);
  });
  it("treats float-noise duplicates as one x", () => {
    expect(alignNumeric([{ x: [1 / 12], y: [1] }, { x: [0.08333333333333333], y: [2] }]).x).toHaveLength(1);
  });
});

describe("runs / inversions", () => {
  it("runs are inclusive [first, last]", () => {
    expect(runs(["a", "b", "c", "d"], (i) => i === 1 || i === 2)).toEqual([["b", "c"]]);
  });
  it("inversion runs newest first, with length and depth; an open run reaches the last point", () => {
    const idx = ["2019-05", "2019-06", "2019-07", "2022-10", "2022-11", "2022-12"];
    const inv = inversions(idx, [-5, -12, 3, null, -40, -60]);
    expect(inv).toEqual([
      { start: "2022-11", end: "2022-12", n: 2, min: -60, open: true },
      { start: "2019-05", end: "2019-06", n: 2, min: -12, open: false },
    ]);
  });
});

describe("nberBands", () => {
  it("a recession runs to the end of its trough month; spans ending before `from` are dropped", () => {
    expect(nberBands([{ start: "2007-12-01", end: "2009-06-01" }, { start: "2020-02-01", end: "2020-04-01" }], "2010-01-01")).toEqual([{ from: "2020-02-01", to: "2020-04-30", tone: "faint" }]);
  });
});

describe("argmaxStates / regimeBands", () => {
  it("most likely state per row; spans of non-calm states, the most volatile hatched", () => {
    const st = argmaxStates(["p0", "p1", "p2"], { p0: [0.8, 0.2, 0.1, 0.7], p1: [0.1, 0.7, 0.2, 0.2], p2: [0.1, 0.1, 0.7, 0.1] }, 4);
    expect(st).toEqual([0, 1, 2, 0]);
    expect(regimeBands(["w1", "w2", "w3", "w4"], st, 3)).toEqual([
      { from: "w2", to: "w2", tone: "faint" },
      { from: "w3", to: "w3", tone: "hatch" },
    ]);
  });
});

describe("seriesStyle", () => {
  it("cycles ink tones, then dashes; never signal", () => {
    const s = Array.from({ length: 9 }, (_, i) => seriesStyle(i));
    expect(s.slice(0, 4)).toEqual([
      { tone: "ink", dash: "solid" },
      { tone: "ink2", dash: "solid" },
      { tone: "ink3", dash: "solid" },
      { tone: "ink", dash: "dash" },
    ]);
    expect(new Set(s.map((x) => `${x.tone}${x.dash}`)).size).toBe(9);
    expect(s.some((x) => (x.tone as string) === "signal")).toBe(false);
  });
});

describe("P6 regime.hmm tables", () => {
  it("probs rows are dated, ordered, and coerced; the latest filtered week skips a trailing null", () => {
    const rows = probRows([
      { date: "2026-09-25T00:00:00", p_high: 0.4, p_high_smoothed_history: 0.5, p0: 0.6 },
      { date: "2026-09-18", p_high: 0.2, p_high_smoothed_history: "x" },
      { date: null, p_high: 0.9 },
      { date: "2026-10-02", p_high: null, p_high_smoothed_history: 0.7 },
    ]);
    expect(rows.map((r) => r.date)).toEqual(["2026-09-18", "2026-09-25", "2026-10-02"]);
    expect(rows[0].p_high_smoothed_history).toBeNull();
    expect(latestProb(rows)?.date).toBe("2026-09-25");
    expect(latestProb([])).toBeNull();
  });
  it("the holdout row is coerced; no row means not evaluated", () => {
    expect(q02Holdout([])).toBeNull();
    expect(q02Holdout(null)).toBeNull();
    const h = q02Holdout([{ holdout_weeks: 180, dm_pvalue: 0.31, hac_t_p: "2", holdout_start: "2022-01-07T00:00:00" }]);
    expect(h?.holdout_weeks).toBe(180);
    expect(h?.hac_t_p).toBeNull();
    expect(h?.holdout_start).toBe("2022-01-07");
  });
});
