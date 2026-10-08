import { describe, expect, it } from "vitest";
import { CHARTER, backtestVerdict, costRows, costsVerdict, failedGates, farmCounts, growthCurve, holdoutDates, pboHigh, sweepVerdict, walkForwardVerdict } from "./derive";
import type { CostsOut, FarmBoardRow, FarmCellRow, RunOut, SweepOut, WalkForwardOut } from "./types";

const run = (psr: number | null, probLe0: number | null, audit = true) =>
  ({
    psr: { psr_vs_0: psr, reference: "" },
    bootstrap_sharpe: probLe0 === null ? { error: "too short" } : { prob_sharpe_le_0: probLe0, ci_low: -0.1, ci_high: 0.9 },
    look_ahead_audit: { passed: audit, checked: [], max_abs_diff: 0, points: 3 },
  }) as unknown as RunOut;

describe("backtestVerdict (one run is never promoted: charter, 'nothing on a single backtest')", () => {
  it("ADVISORY at best, naming what a single run cannot test", () => {
    const v = backtestVerdict(run(0.97, 0.01));
    expect(v.value).toBe("ADVISORY");
    expect(v.detail).toBe("SINGLE RUN · DSR · HOLDOUT · COSTS NOT RUN");
    expect(v.gates.find((g) => g.code === "PSR")?.status).toBe("PASS");
    expect(v.gates.find((g) => g.code === "DSR")?.status).toBe("NOT RUN");
  });
  it("FAIL on the charter's PSR >= 0.70 and on the bootstrap's lower 5th percentile (P(SR <= 0) >= 5%)", () => {
    const v = backtestVerdict(run(0.62, 0.18));
    expect(v.value).toBe("FAIL");
    expect(v.detail).toBe("PSR 0.62 < 0.70 · P(SR≤0) 18.0% ≥ 5%");
    expect(backtestVerdict(run(0.9, 0.05)).value).toBe("FAIL");
    expect(backtestVerdict(run(0.7, 0.049)).value).toBe("ADVISORY");
  });
  it("a failed look-ahead audit fails the run", () => {
    const v = backtestVerdict(run(0.99, 0.0, false));
    expect(v.value).toBe("FAIL");
    expect(v.detail).toContain("LOOK-AHEAD");
  });
  it("INSUFFICIENT DATA when neither PSR nor the bootstrap could be computed", () => {
    expect(backtestVerdict(run(null, null)).value).toBe("INSUFFICIENT DATA");
  });
});

const sweep = (dsr: number | null, psr: number | null, pbo: number | null) =>
  ({
    deflated_sharpe: { dsr, psr_vs_0: psr, n_trials: 25, sharpe_ann: 0.8, sr0_annualized: 0.6, selected: 3 },
    pbo: pbo === null ? { error: "too few rows" } : { pbo, n_combinations: 12870 },
    spa: { pvalue_consistent: 0.21 },
  }) as unknown as SweepOut;

describe("sweepVerdict (DSR deflated by every combination tried)", () => {
  it("FAIL below the charter's DSR 0.30, with a high PBO named", () => {
    const v = sweepVerdict(sweep(0.12, 0.91, 0.62));
    expect(v.value).toBe("FAIL");
    expect(v.detail).toBe("DSR 0.12 < 0.30 · PBO 0.62 HIGH");
    expect(v.gates.find((g) => g.code === "PBO")?.status).toBe("REPORTED");
  });
  it("ADVISORY when DSR and PSR clear: a sweep is in-sample, the holdout is the walk-forward's", () => {
    const v = sweepVerdict(sweep(0.55, 0.93, 0.2));
    expect(v.value).toBe("ADVISORY");
    expect(v.detail).toBe("IN-SAMPLE · HOLDOUT NOT RUN");
  });
  it("INSUFFICIENT DATA without a DSR", () => {
    expect(sweepVerdict(sweep(null, null, null)).value).toBe("INSUFFICIENT DATA");
  });
});

const wf = (ret: number | null, psr: number | null, wfe: number | null) =>
  ({ oos_summary: { total_return: ret, psr_vs_0: psr }, walk_forward_efficiency: wfe }) as unknown as WalkForwardOut;

describe("walkForwardVerdict (the charter's holdout gate)", () => {
  it("FAIL when the stitched out-of-sample record lost money", () => {
    const v = walkForwardVerdict(wf(-0.04, 0.3, 0.1));
    expect(v.value).toBe("FAIL");
    expect(v.detail).toBe("HOLDOUT −4.0% ≤ 0 · PSR 0.30 < 0.70");
  });
  it("ADVISORY when positive: DSR and regimes are the farm's", () => {
    expect(walkForwardVerdict(wf(0.21, 0.8, 0.64))).toMatchObject({ value: "ADVISORY", detail: "WFE 64% · DSR · REGIMES NOT RUN" });
  });
  it("INSUFFICIENT DATA without an out-of-sample return", () => {
    expect(walkForwardVerdict(wf(null, null, null)).value).toBe("INSUFFICIENT DATA");
  });
});

const costs = (curve: [number, number | null][], be: number | null) =>
  ({ curve: curve.map(([cost_bps, sharpe]) => ({ cost_bps, sharpe, cagr: null, ann_vol: null, max_drawdown: null, annual_cost_drag: null })), breakeven_bps_sharpe_zero: be }) as unknown as CostsOut;

describe("costsVerdict", () => {
  it("FAIL when the Sharpe is not positive at the cost the user pays", () => {
    const v = costsVerdict(costs([[0, 0.3], [5, -0.1], [15, -0.4], [30, -0.9]], 3.1), 5);
    expect(v.value).toBe("FAIL");
    expect(v.detail).toBe("SR −0.10 AT 5 BP · BREAK-EVEN 3.1 BP");
  });
  it("ADVISORY with the break-even and the margin over the user's cost; the 0/5/15/30 grid is REPORTED", () => {
    const v = costsVerdict(costs([[0, 0.9], [5, 0.8], [15, 0.6], [30, 0.3]], 46), 5);
    expect(v).toMatchObject({ value: "ADVISORY", detail: "BREAK-EVEN 46 BP · 9.2× YOUR 5 BP" });
    expect(v.gates.find((g) => g.code === "COSTS")?.status).toBe("REPORTED");
  });
  it("names the charter levels a ladder left out", () => {
    const v = costsVerdict(costs([[0, 0.9], [10, 0.8]], null), 10);
    expect(v.gates.find((g) => g.code === "COSTS")).toMatchObject({ status: "NOT RUN", value: "MISSING 5 · 15 · 30" });
  });
  it("INSUFFICIENT DATA when the user's level is not on the ladder or the ladder is empty", () => {
    expect(costsVerdict(costs([], null), 5).value).toBe("INSUFFICIENT DATA");
    expect(costsVerdict(costs([[0, 0.9], [10, 0.5]], null), 5).value).toBe("INSUFFICIENT DATA");
  });
  it("the charter grid is 0/5/15/30 bps", () => expect(CHARTER.costs).toEqual([0, 5, 15, 30]));
});

describe("farm leaderboard", () => {
  it("failedGates reads the gate names out of the artifact's own verdict detail (never recomputed)", () => {
    expect(failedGates("failed: dsr 0.12 (needs >= 0.3), bootstrap_lower_5pct -0.04 (needs > 0), regimes_positive 2.00 (needs >= 3 of 5); PBO 0.71 (high)")).toEqual(["dsr", "bootstrap_lower_5pct", "regimes_positive"]);
    expect(failedGates("failed: psr n/a (needs >= 0.7)")).toEqual(["psr"]);
    expect(failedGates("every charter gate passed; advisory; never sized")).toEqual([]);
    expect(failedGates(null)).toEqual([]);
  });
  it("failedGates and pboHigh read the label form too (FAILED DSR 0.12 (NEEDS >= 0.3) · PBO 0.71 HIGH)", () => {
    const d = "FAILED DSR 0.12 (NEEDS >= 0.3), BOOTSTRAP_LOWER_5PCT -0.04 (NEEDS > 0), COST_SWEEP N/A (NEEDS 0/5/15/30 BPS REPORTED) · PBO 0.71 HIGH";
    expect(failedGates(d)).toEqual(["dsr", "bootstrap_lower_5pct", "cost_sweep"]);
    expect(pboHigh(d)).toBe(true);
    expect(failedGates("EVERY CHARTER GATE PASSED · ADVISORY · NEVER SIZED")).toEqual([]);
    expect(pboHigh("EVERY CHARTER GATE PASSED · ADVISORY · NEVER SIZED")).toBe(false);
  });
  it("pboHigh finds the PBO the verdict named as high", () => {
    expect(pboHigh("failed: dsr 0.12 (needs >= 0.3); PBO 0.71 (high)")).toBe(true);
    expect(pboHigh("every charter gate passed; advisory; never sized")).toBe(false);
  });
  it("farmCounts: failures, passes, skipped cells and the total", () => {
    const board = [{ verdict: "FAIL" }, { verdict: "FAIL" }, { verdict: "PASS" }, { verdict: "INSUFFICIENT DATA" }] as FarmBoardRow[];
    const cells = [...board.map(() => ({ status: "ok" })), { status: "skipped" }, { status: "skipped" }] as FarmCellRow[];
    expect(farmCounts(board, cells)).toEqual({ fail: 2, pass: 1, insufficient: 1, skipped: 2, total: 6 });
  });
  it("costRows parses a cell's cost curve JSON; garbage is no rows", () => {
    expect(costRows('[{"cost_bps": 0.0, "sharpe": 0.4, "cagr": 0.05}, {"cost_bps": 30.0, "sharpe": -0.1, "cagr": -0.01}]')).toEqual([
      { cost_bps: 0, sharpe: 0.4, cagr: 0.05 },
      { cost_bps: 30, sharpe: -0.1, cagr: -0.01 },
    ]);
    expect(costRows("not json")).toEqual([]);
    expect(costRows(null)).toEqual([]);
  });
});

describe("models holdout", () => {
  it("growthCurve compounds net returns into growth of $1, keyed by date or index", () => {
    const g = growthCurve([
      { date: "2024-01-31", model_net: 0.1, composite_net: 0 },
      { date: "2024-02-29", model_net: -0.5, composite_net: 0.1 },
    ]);
    expect(g.x).toEqual(["2024-01-31", "2024-02-29"]);
    expect(g.model.map((v) => Math.round(v * 1000) / 1000)).toEqual([1.1, 0.55]);
    expect(g.composite.map((v) => Math.round((v ?? NaN) * 1000) / 1000)).toEqual([1, 1.1]);
    expect(growthCurve([{ index: "2024-01-31", model_net: 0.02, composite_net: null }]).composite).toEqual([null]);
  });
  it("holdoutDates finds the date column whatever the frame called it", () => {
    expect(holdoutDates({ index: "2024-01-31" })).toBe("2024-01-31");
    expect(holdoutDates({ date: "2024-01-31T00:00:00" })).toBe("2024-01-31T00:00:00");
    expect(holdoutDates({ x: 1 })).toBeNull();
  });
});
