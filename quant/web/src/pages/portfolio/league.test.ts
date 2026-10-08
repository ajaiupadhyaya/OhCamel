import { describe, expect, it } from "vitest";
import { leagueRows } from "./league";

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
