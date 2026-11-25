import { describe, expect, it } from "vitest";
import { grsLine, shankenFlips, sourceLine } from "./crossSection";
import type { CrossSectionOut } from "./types";

const d: CrossSectionOut = {
  model: { factors: [{ name: "MKT", long: "SPY", short: null }, { name: "SIZE", long: "IWM", short: "SPY" }], tickers: ["XLE", "XLF"] },
  premia: [
    { term: "MKT", premium: 0.004, se_nw: 0.002, t_nw: 2.05, se_shanken: 0.00205, t_shanken: 1.95 },
    { term: "SIZE", premium: 0.001, se_nw: 0.004, t_nw: 0.25, se_shanken: 0.004, t_shanken: 0.25 },
  ],
  grs: { statistic: 1.5, p_value: 0.17, n_assets: 6, n_factors: 2, n_obs: 264, mean_abs_alpha_monthly: 0.001, alphas: [] },
  stats: { start: "2007-11-30", end: "2026-10-31", n_periods: 228, nw_lags: 4, shanken_c: 1.01, beta_window: 36, mean_assets: 6, mean_r2: 0.6, grs_start: "2004-11-30", grs_end: "2026-10-31" },
  method: {},
};

describe("cross-section wording", () => {
  it("states the GRS test, its degrees of freedom, verdict and as-of", () => {
    expect(grsLine(d)).toBe("GRS F(6, 256) = 1.50 · p 0.170 · all 6 alphas zero not rejected · 264 months to 2026-10");
    expect(grsLine({ ...d, grs: { ...d.grs, p_value: 0.0004 } })).toContain("p <0.001 · all 6 alphas zero rejected at 1%");
  });
  it("names the source, legs, beta window and as-of", () => {
    expect(sourceLine(d)).toContain("SIZE = IWM − SPY");
    expect(sourceLine(d)).toContain("MKT = SPY − T-BILL");
    expect(sourceLine(d)).toContain("36 months before each month · as of 2026-10");
  });
  it("lists the premia whose significance Shanken's correction removes", () => {
    expect(shankenFlips(d)).toEqual(["MKT"]);
  });
});
