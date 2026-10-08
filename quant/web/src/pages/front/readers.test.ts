import { describe, expect, it } from "vitest";
import { artifacts24h, atlasFront, farmTop, regimeFront, REGIME_WEEKS } from "./readers";
import type { FarmBoardRow } from "../research/types";

const atlasRec = (book: string, method: string, alpha: number, horizon: number, v: number, es: number, extra: Record<string, unknown> = {}) => ({ book, method, alpha, horizon, var: v, es, n_paths: null, ...extra });

describe("atlasFront (risk.mc_atlas · summary)", () => {
  const recs = [
    atlasRec("core", "fhs", 0.95, 1, 0.0077, 0.0108),
    atlasRec("core", "fhs", 0.99, 1, 0.0128, 0.0154, { var_usd: 1275, es_usd: 1541, notional: 100000, observations: 504 }),
    atlasRec("core", "copula_t", 0.99, 1, 0.0141, 0.0187, { var_usd: 1410, es_usd: 1870 }),
    atlasRec("core", "fhs", 0.99, 10, 0.04, 0.05),
    atlasRec("growth", "fhs", 0.99, 1, 0.02, 0.025),
  ];
  it("reads the core book at 99% · 1D, FHS beside the t-copula", () => {
    const a = atlasFront(recs);
    expect(a).not.toBeNull();
    expect(a!.book).toBe("core");
    expect(a!.fhs).toEqual({ var: 0.0128, es: 0.0154, varUsd: 1275, esUsd: 1541 });
    expect(a!.tcop).toEqual({ var: 0.0141, es: 0.0187, varUsd: 1410, esUsd: 1870 });
    expect(a!.books).toBe(2);
    expect(a!.notional).toBe(100000);
  });
  it("falls back to the first book when there is no core", () => {
    expect(atlasFront(recs.filter((r) => r.book !== "core"))!.book).toBe("growth");
  });
  it("no row at 99% · 1D → null, and a missing method is null not zero", () => {
    expect(atlasFront([atlasRec("core", "fhs", 0.95, 1, 0.01, 0.02)])).toBeNull();
    expect(atlasFront([recs[1]])!.tcop).toBeNull();
  });
  it("an unknown shape → null", () => {
    expect(atlasFront([{ foo: 1 }])).toBeNull();
    expect(atlasFront(null)).toBeNull();
  });
});

const board = (cell_id: string, verdict: string, dsr: number | null): FarmBoardRow => {
  const [strategy, universe] = cell_id.split("@");
  return { cell_id, strategy, universe, verdict, detail: null, dsr_farm: dsr, psr: null, oos_sharpe_ann: null, holdout_return: null, boot_lo5: null, pbo: null, spa_p: null, n_combos: null, selected_params: null, data_asof: null, ran_at: null };
};

describe("farmTop (farm.sweep · leaderboard)", () => {
  it("the top three by farm DSR, nulls last", () => {
    const rows = [board("a@x", "FAIL", 0.1), board("b@x", "FAIL", null), board("c@x", "PASS", 0.42), board("d@x", "FAIL", 0.2), board("e@x", "FAIL", 0.05)];
    expect(farmTop(rows).map((r) => r.cell_id)).toEqual(["c@x", "d@x", "a@x"]);
    expect(farmTop(rows, 5).map((r) => r.cell_id)).toEqual(["c@x", "d@x", "a@x", "e@x", "b@x"]);
  });
  it("does not reorder its input", () => {
    const rows = [board("a@x", "FAIL", 0.1), board("c@x", "PASS", 0.42)];
    farmTop(rows);
    expect(rows[0].cell_id).toBe("a@x");
  });
});

describe("regimeFront (regime.hmm · probs)", () => {
  const week = (i: number) => new Date(Date.UTC(2024, 0, 5 + 7 * i)).toISOString().slice(0, 10);
  const probs = Array.from({ length: 130 }, (_, i) => ({ date: week(i), p_high: i === 129 ? null : i / 200, p_high_smoothed_history: 0.3 }));
  it("the latest filtered week, its state at the 50% line, and the last two years for the chart", () => {
    const r = regimeFront(probs)!;
    expect(r.last.date).toBe(week(128));
    expect(r.last.p_high).toBeCloseTo(0.64);
    expect(r.state).toBe("HIGH VOL");
    expect(r.window.length).toBe(REGIME_WEEKS);
    expect(r.window[r.window.length - 1].date).toBe(week(129));
  });
  it("below 50% is not the high-vol state", () => {
    expect(regimeFront([{ date: "2026-10-02", p_high: 0.2, p_high_smoothed_history: null }])!.state).toBe("NOT HIGH VOL");
  });
  it("no filtered week → null", () => {
    expect(regimeFront([{ date: "2026-10-02", p_high: null, p_high_smoothed_history: 0.4 }])).toBeNull();
    expect(regimeFront([])).toBeNull();
  });
});

describe("artifacts24h (GET /api/jobs · last night's artifacts)", () => {
  const now = Date.parse("2026-10-07T12:00:00Z");
  const j = (id: string, finished_at: string | null, artifact_id: string | null, state = "done") => ({ id, kind: "k", state, finished_at, artifact_id });
  it("counts done jobs with an artifact that finished in the last 24h", () => {
    const rows = [j("a", "2026-10-07T03:00:00Z", "A"), j("b", "2026-10-06T11:00:00Z", "B"), j("c", "2026-10-07T04:00:00Z", null, "failed"), j("d", null, null, "running"), j("e", "2026-10-06T13:00:00Z", "E")];
    expect(artifacts24h(rows, now)).toBe(2);
  });
  it("no list → null", () => expect(artifacts24h(undefined, now)).toBeNull());
});
