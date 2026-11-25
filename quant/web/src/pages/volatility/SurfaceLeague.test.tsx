import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ThemeProvider } from "../../lib/theme";
import { SurfaceLeagueReport, SurfaceModelLine } from "./SurfaceLeague";
import type { SurfaceLeagueRow } from "./types";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

const row = (model: string, over: Partial<SurfaceLeagueRow> = {}): SurfaceLeagueRow => ({
  underlying: "SPY", model, asof: "2026-11-24", n_slices: 31, n_quotes: 5000, rmse_vol_pts: 0.48,
  butterfly_violations: 0, calendar_violations: 3, calendar_pairs: 30, next_day_rmse_vol_pts: 1.25, next_day_n: 4800,
  next_day_unscored: 120, next_day_fit_asof: "2026-11-23", next_day_note: null, params: null, fit_note: null, ...over,
});

describe("SurfaceLeagueReport (vol.surface_league)", () => {
  const rows = [
    row("SVI (per slice)"),
    row("SSVI (power law)", { rmse_vol_pts: 3.45, calendar_violations: 0, next_day_rmse_vol_pts: 3.6, params: '{"rho": -0.61, "eta": 1.24, "gamma": 0.499}' }),
    row("SVI (per slice)", { underlying: "QQQ", rmse_vol_pts: 9.99 }),
  ];
  const html = render(<SurfaceLeagueReport rows={rows} ticker="spy" />);

  it("shows this name's two rows with the three scores, in vol points and violation counts", () => {
    expect(html).toContain("SVI (per slice)");
    expect(html).toContain("SSVI (power law)");
    expect(html).toContain("0.48");
    expect(html).toContain("3.45");
    expect(html).toContain("3 / 30");
    expect(html).toContain("0 / 30");
    expect(html).toContain("1.25");
    expect(html).toContain("2026-11-23");
    expect(html).not.toContain("9.99");
  });
  it("leaves a next-day cell blank with its reason, never a number", () => {
    const h = render(<SurfaceLeagueReport rows={[row("SVI (per slice)", { next_day_rmse_vol_pts: null, next_day_n: null, next_day_fit_asof: null, next_day_note: "no earlier snapshot day for this underlying" })]} ticker="SPY" />);
    expect(h).toContain("NO EARLIER DAY");
    expect(h).toContain("no earlier snapshot day for this underlying");
  });
  it("says when the name is not in the league", () => {
    expect(render(<SurfaceLeagueReport rows={rows} ticker="IWM" />)).toContain("NOT IN THE LEAGUE");
  });
});

describe("SurfaceModelLine (live surface)", () => {
  it("reads the SSVI parameters against the condition and both models' live violations", () => {
    const h = render(
      <SurfaceModelLine
        ssvi={{ error: null, grid_iv: [[0.2]], method: "", violations: { butterfly_violations: 0, calendar_violations: 0, calendar_pairs: 30, n_slices: 31 },
          fit: { params: { rho: -0.61, eta: 1.24, gamma: 0.499, theta: [], T: [], eta_one_plus_abs_rho: 1.996 }, n: 5000, rmse_vol_pts: 3.45, max_abs_err_vol_pts: 12, slice_rmse_vol_pts: [], slice_n: [], cost: 1, nfev: 20, status: 2, conditions: {} } }}
        sviViolations={{ butterfly_violations: 0, calendar_violations: 2, calendar_pairs: 30, n_slices: 31 }}
      />,
    );
    expect(h).toContain("1.996");
    expect(h).toContain("3.45");
    expect(h).toContain("2 / 30");
  });
});
