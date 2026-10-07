import { describe, expect, it } from "vitest";
import { annualVol, coneLines, dayTicks, focusWindow, historyFor, leagueFor, leagueSummary, nameForecasts, roundTo, signedParts } from "./derive";
import type { ConeRow } from "./types";
import type { HistoryRow, LeagueRow, LeagueSummaryRow } from "./derive";

describe("annualVol", () => {
  it("a daily variance as an annualized vol (sqrt(252 v)); null for missing or negative", () => {
    expect(annualVol(1e-4)).toBeCloseTo(Math.sqrt(252e-4), 12);
    expect(annualVol(null)).toBeNull();
    expect(annualVol(-1)).toBeNull();
    expect(annualVol(Number.NaN)).toBeNull();
  });
});

const lg = (ticker: string, model: string, qlike: number, rank: number, in_mcs: boolean | null): LeagueRow => ({ ticker, model, qlike, mse: 1e-8, n: 250, in_mcs, rank_qlike: rank, target: "squared_return" });

describe("leagueSummary / leagueFor (P2 forecast league)", () => {
  it("ranks models by their MCS rate, then mean QLIKE rank; an unknown rate sorts last", () => {
    const rows: LeagueSummaryRow[] = [
      { model: "ewma", names: 40, mean_rank: 3.1, mcs_rate: 0.5, median_qlike: -8.1 },
      { model: "gjr", names: 40, mean_rank: 1.9, mcs_rate: 0.9, median_qlike: -8.3 },
      { model: "har", names: 12, mean_rank: 2.0, mcs_rate: null, median_qlike: -8.2 },
      { model: "garch", names: 40, mean_rank: 2.4, mcs_rate: 0.9, median_qlike: -8.2 },
    ];
    expect(leagueSummary(rows).map((r) => r.model)).toEqual(["gjr", "garch", "ewma", "har"]);
  });
  it("one name's rows, best QLIKE rank first", () => {
    const rows = [lg("SPY", "ewma", -8, 3, false), lg("QQQ", "gjr", -7, 1, true), lg("SPY", "gjr", -8.4, 1, true), lg("SPY", "garch", -8.2, 2, true)];
    expect(leagueFor(rows, "spy").map((r) => r.model)).toEqual(["gjr", "garch", "ewma"]);
    expect(leagueFor(rows, "IWM")).toEqual([]);
  });
});

describe("nameForecasts", () => {
  it("next-day forecasts per model as annualized vols, plus the HAR 22-day; null when the name is absent", () => {
    const f = nameForecasts([{ ticker: "SPY", asof: "2026-10-06", var_1d_gjr: 1e-4, var_1d_ewma: 4e-4, har_22d: 2.5e-4 }], "SPY");
    expect(f?.asof).toBe("2026-10-06");
    expect(f?.models.map((m) => m.model)).toEqual(["gjr", "ewma"]);
    expect(f?.models[1].vol).toBeCloseTo(Math.sqrt(252 * 4e-4), 12);
    expect(f?.har22).toBeCloseTo(Math.sqrt(252 * 2.5e-4), 12);
    expect(nameForecasts([], "SPY")).toBeNull();
  });
});

const h = (underlying: string, asof: string, mf: number | null, vrp: number | null): HistoryRow => ({ underlying, asof, atm_iv_30d: 0.2, atm_iv_90d: 0.22, term_slope: 0.02, rr25_30d: -0.04, bf25_30d: 0.01, mf_var_30d: mf, vrp, vrp_note: vrp === null ? "no HAR forecast" : null, n_slices: 8, spot: 500 });

describe("historyFor (P7 surface history)", () => {
  it("one underlying, oldest first, with the 30-day model-free vol and the HAR vol it was compared with", () => {
    const rows = historyFor([h("SPY", "2026-10-06", 0.04, 0.0148), h("QQQ", "2026-10-05", 0.05, null), h("SPY", "2026-10-05", 0.0361, null)], "spy");
    expect(rows.map((r) => r.asof)).toEqual(["2026-10-05", "2026-10-06"]);
    expect(rows[1].mf_vol_30d).toBeCloseTo(0.2, 12);
    // vrp = mf_var - 252 har  =>  har vol = sqrt(mf_var - vrp)
    expect(rows[1].har_vol_22d).toBeCloseTo(Math.sqrt(0.04 - 0.0148), 12);
    expect(rows[1].vrp_pts).toBeCloseTo(0.2 - Math.sqrt(0.0252), 12);
    expect(rows[0].har_vol_22d).toBeNull();
    expect(rows[0].vrp_pts).toBeNull();
  });
});

describe("coneLines", () => {
  const cone: ConeRow[] = [10, 21].map((horizon, i) => ({ horizon, min: 0.05, max: 0.6, mean: 0.15, p10: 0.08, p25: 0.1, p50: 0.13 + i * 0.01, p75: 0.17, p90: 0.24, current: 0.11, current_pctile: 0.3, n_windows: 2500 }));
  it("P90 P75 MED P25 P10 as quiet lines and TODAY in ink with points", () => {
    const s = coneLines(cone);
    expect(s.map((x) => x.name)).toEqual(["P90", "P75", "MED", "P25", "P10", "TODAY"]);
    expect(s.find((x) => x.name === "MED")?.y).toEqual([0.13, 0.14]);
    expect(s.find((x) => x.name === "TODAY")?.tone).toBe("ink");
    expect(s.filter((x) => x.name !== "TODAY").every((x) => x.tone !== "signal")).toBe(true);
  });
});

describe("dayTicks", () => {
  it("only the calendar ticks the days span, labelled in caps", () => {
    expect(dayTicks([8, 40, 200])).toEqual([
      { at: 7, label: "7D" },
      { at: 14, label: "14D" },
      { at: 30, label: "30D" },
      { at: 60, label: "60D" },
      { at: 90, label: "90D" },
      { at: 180, label: "180D" },
    ]);
    expect(dayTicks([300, 800]).map((t) => t.label)).toEqual(["1Y", "2Y"]);
  });
});

describe("focusWindow", () => {
  it("frames spot, strikes and breakevens with padding, inside the grid", () => {
    const [lo, hi] = focusWindow({ spot: 100, breakevens: [95, 105], strikes: [100], grid: [50, 200] });
    expect(lo).toBeLessThan(95);
    expect(hi).toBeGreaterThan(105);
    expect(lo).toBeGreaterThanOrEqual(50);
    expect(hi).toBeLessThanOrEqual(200);
  });
});

describe("signedParts", () => {
  it("splits a series into its non-negative and negative parts (null elsewhere)", () => {
    expect(signedParts([0.1, -0.2, null, 0])).toEqual({ pos: [0.1, null, null, 0], neg: [null, -0.2, null, null] });
  });
});

describe("roundTo", () => {
  it("rounds an input value to n decimals", () => {
    expect(roundTo(123.4567, 2)).toBe(123.46);
    expect(roundTo(0.123456789, 6)).toBe(0.123457);
  });
});
