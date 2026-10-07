import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import { LeagueReport } from "./Forecasts";
import { HistoryReport } from "./History";
import type { DmRow, HistoryRow, LeagueRow, LeagueSummaryRow } from "./derive";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

const summary: LeagueSummaryRow[] = [
  { model: "ewma", names: 40, mean_rank: 3.4, mcs_rate: 0.35, median_qlike: -8.1 },
  { model: "gjr", names: 40, mean_rank: 1.7, mcs_rate: 0.92, median_qlike: -8.4 },
];
const lg = (model: string, rank: number, in_mcs: boolean | null): LeagueRow => ({ ticker: "SPY", model, qlike: -8 - 0.1 * (3 - rank), mse: 2.5e-8, n: 250, in_mcs, rank_qlike: rank, target: "squared_return" });
const league = [lg("ewma", 3, false), lg("gjr", 1, true), lg("garch", 2, true)];
const dm: DmRow[] = [{ ticker: "SPY", a: "gjr", b: "ewma", stat: -2.8, pvalue: 0.005 }];

describe("LeagueReport (P2 vol.forecast_league)", () => {
  const html = render(<LeagueReport summary={summary} league={league} dm={dm} forecasts={[{ ticker: "SPY", asof: "2026-10-06", var_1d_gjr: 1e-4, var_1d_ewma: 1.21e-4 }]} skipped={[{ ticker: "XYZ", reason: "900 sessions (< 1250)" }]} dropped={[]} ticker="spy" />);

  it("ranks the models by MCS rate: GJR before EWMA", () => {
    expect(html.indexOf("GJR")).toBeLessThan(html.indexOf("EWMA"));
    expect(html).toContain("92%");
  });
  it("shows this name's league best rank first, MCS IN / OUT, and next-day forecasts annualized", () => {
    expect(html).toContain("SPY · out of sample");
    expect(html).toMatch(/>IN</);
    expect(html).toMatch(/>OUT</);
    expect(html).toContain("15.9%"); // sqrt(252e-4)
    expect(html).toContain("NO MINUTE BARS");
  });
  it("lists the DM pair and the skipped names with their reason; no signal colour on a measurement", () => {
    expect(html).toContain("−2.80");
    expect(html).toContain("1250");
    expect(html).not.toMatch(/class="[^"]*\bloss\b/);
  });
  it("a name outside the league says so instead of showing numbers", () => {
    const h = render(<LeagueReport summary={summary} league={league} dm={dm} forecasts={[]} skipped={[]} dropped={[]} ticker="IWM" />);
    expect(h).toContain("IWM NOT IN THE LEAGUE");
    expect(h).toContain("INSUFFICIENT DATA");
  });
});

const row = (asof: string, mf: number, vrp: number | null): HistoryRow => ({ underlying: "SPY", asof, atm_iv_30d: 0.18, atm_iv_90d: 0.2, term_slope: 0.02, rr25_30d: -0.045, bf25_30d: 0.012, mf_var_30d: mf, vrp, vrp_note: vrp === null ? "no HAR forecast dated on or before the snapshot" : null, n_slices: 9, spot: 671 });

describe("HistoryReport (P7 vol.surface_history)", () => {
  it("says how many days it holds; one day draws no line and says why", () => {
    const html = render(<HistoryReport history={[row("2026-10-06", 0.04, 0.0148)]} summary={[{ underlying: "SPY", days: 1, first: "2026-10-06", last: "2026-10-06" }]} errors={[]} ticker="SPY" />);
    expect(html).toContain("SPY · 1 DAY");
    expect(html).toContain("A LINE NEEDS 2");
    expect(html).toContain("20.0%"); // sqrt(0.04)
    expect(html).toContain("+4.1"); // 0.2 - sqrt(0.0252) = 0.0413 -> +4.1 pts
  });
  it("a day without a HAR forecast reads NO HAR, never a number; an absent underlying says so", () => {
    const html = render(<HistoryReport history={[row("2026-10-05", 0.0361, null), row("2026-10-06", 0.04, 0.0148)]} summary={[]} errors={[{ underlying: "QQQ", asof: "2026-10-06", error: "no ingest_log row" }]} ticker="SPY" />);
    expect(html).toContain("SPY · 2 DAYS");
    expect(html).toContain("NO HAR");
    expect(html).toContain("no ingest_log row");
    const none = render(<HistoryReport history={[row("2026-10-06", 0.04, null)]} summary={[]} errors={[]} ticker="TLT" />);
    expect(none).toContain("NO SNAPSHOTS FOR TLT");
  });
});
