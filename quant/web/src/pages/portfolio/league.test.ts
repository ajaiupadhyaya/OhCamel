import { describe, expect, it } from "vitest";
import { leagueGroups, leagueRows } from "./league";

describe("leagueRows (cov.league league table)", () => {
  it("sorts estimators by out-of-sample min-variance vol and measures each against the sample", () => {
    const rows = leagueRows([
      { estimator: "sample", oos_vol: 0.12 },
      { estimator: "ledoit_wolf", oos_vol: 0.1, lw_p: 0.03 },
      { estimator: "pca_3", realized_vol: 0.11 },
    ])!;
    expect(rows.map((r) => r.estimator)).toEqual(["ledoit_wolf", "pca_3", "sample"]);
    expect(rows[0].rank).toBe(1);
    expect(rows[0].vsSample).toBeCloseTo(-0.02);
    expect(rows[0].lwP).toBe(0.03);
    expect(rows[2].vsSample).toBe(0);
  });
  it("vs sample is null when there is no sample row", () => {
    expect(leagueRows([{ estimator: "ewma", oos_vol: 0.1 }])![0].vsSample).toBeNull();
  });
  it("returns null on an unknown shape", () => {
    expect(leagueRows([{ name: "x" }])).toBeNull();
    expect(leagueRows(undefined)).toBeNull();
  });
});

describe("leagueGroups (one league per universe)", () => {
  const two = [
    { universe: "sectors", estimator: "sample", realized_vol: 0.15 },
    { universe: "sectors", estimator: "ledoit_wolf", realized_vol: 0.12, lw_p: 0.01 },
    { universe: "site_etfs", estimator: "sample", realized_vol: 0.03 },
    { universe: "site_etfs", estimator: "ledoit_wolf", realized_vol: 0.02, lw_p: 0.2 },
    { universe: "site_etfs", estimator: "ewma", realized_vol: 0.025 },
  ];
  it("ranks and measures vs sample within each universe, in artifact order", () => {
    const g = leagueGroups(two)!;
    expect(g.map((x) => x.universe)).toEqual(["sectors", "site_etfs"]);
    expect(g[0].rows.map((r) => [r.rank, r.estimator])).toEqual([[1, "ledoit_wolf"], [2, "sample"]]);
    expect(g[1].rows.map((r) => [r.rank, r.estimator])).toEqual([[1, "ledoit_wolf"], [2, "ewma"], [3, "sample"]]);
    const s = g[1].rows.find((r) => r.estimator === "sample")!;
    expect(s.vsSample).toBe(0);
    expect(g[1].rows[0].vsSample).toBeCloseTo(-0.01);
    expect(g[0].rows[0].vsSample).toBeCloseTo(-0.03);
  });
  it("gives every row a key unique across universes", () => {
    const keys = leagueGroups(two)!.flatMap((x) => x.rows.map((r) => r.key));
    expect(new Set(keys).size).toBe(keys.length);
  });
  it("a table without a universe column is one unnamed league", () => {
    const g = leagueGroups([{ estimator: "sample", oos_vol: 0.1 }])!;
    expect(g).toHaveLength(1);
    expect(g[0].universe).toBeNull();
  });
  it("returns null on an unknown shape", () => {
    expect(leagueGroups([{ name: "x" }])).toBeNull();
    expect(leagueGroups(null)).toBeNull();
  });
});
