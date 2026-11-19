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
import { CurrentTable } from "./RealizedTab";
import type { Realized } from "./types";
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
    expect(html).toContain("SHORT HISTORY");
    expect(html).not.toMatch(/class="[^"]*\bloss\b/);
  });
  it("collapses per-name skips to one row per reason code with a name count", () => {
    const fix = (t: string) => ({ ticker: t, reason: `${t}: offline mode and no committed fixture (available: GLD, SPY)` });
    const h = render(<LeagueReport summary={summary} league={league} dm={dm} forecasts={[]} skipped={["A", "AAPL", "ABBV", "ABNB"].map(fix)} dropped={[]} ticker="spy" />);
    expect(h).toContain("OFFLINE · NO FIXTURE");
    expect(h).toContain("A, AAPL, ABBV +1");
    expect(h.match(/OFFLINE · NO FIXTURE/g)).toHaveLength(1);
    expect(h).not.toContain(">ABNB: offline mode");
  });
  it("names the target by its label: the full-day target, the old open-to-close one, the fallback", () => {
    const show = (target: string) =>
      render(<LeagueReport summary={summary} league={league.map((l) => ({ ...l, target }))} dm={dm} forecasts={[]} skipped={[]} dropped={[]} ticker="spy" />);
    expect(show("full_day_rv")).toContain("TARGET MINUTE RV + OVERNIGHT");
    expect(show("minute_rv")).toContain("TARGET MINUTE RV · OPEN→CLOSE ONLY");
    expect(show("squared_return")).toContain("TARGET SQUARED DAILY RETURN");
    expect(show("full_day_rv")).not.toContain("SQUARED DAILY RETURN");
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
  it("a row blanked for its open-to-close basis reads OLD TARGET in the cell, the reason in the tooltip", () => {
    const old = { ...row("2026-10-05", 0.0361, null), vrp_note: "the HAR forecast behind this VRP was of open-to-close variance only: it overstates the premium by the overnight share, so it is not shown" };
    const html = render(<HistoryReport history={[old, row("2026-10-06", 0.04, 0.0148)]} summary={[]} errors={[]} ticker="SPY" />);
    expect(html).toContain(">OLD TARGET<");
    expect(html).toContain("overstates the premium by the overnight share");
    expect(html).not.toContain(">NO HAR<");
  });
  it("a row computed before task 3.4 (basis_note set) is marked OLD BASIS by its date, the reason in the tooltip", () => {
    const old = { ...row("2026-10-05", 0.0361, 0.0148), basis_note: "computed before task 3.4: T from the payload's UTC timestamp read as New York time" };
    const html = render(<HistoryReport history={[old, { ...row("2026-10-06", 0.04, 0.0148), basis_note: null }]} summary={[]} errors={[]} ticker="SPY" />);
    expect(html.match(/>OLD BASIS</g)?.length).toBe(1);
    expect(html).toContain("read as New York time");
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

describe("Realized estimators say what they cover", () => {
  it("the range estimators read OPEN→CLOSE, close-to-close and Yang–Zhang CLOSE→CLOSE", () => {
    const cur = { close_to_close: 0.15, parkinson: 0.12, garman_klass: 0.12, rogers_satchell: 0.12, yang_zhang: 0.15 };
    const covers = { close_to_close: "close_to_close", parkinson: "open_to_close", garman_klass: "open_to_close", rogers_satchell: "open_to_close", yang_zhang: "close_to_close" };
    const html = render(<CurrentTable r={{ current: { "21": cur }, covers } as unknown as Realized} />);
    expect(html.match(/OPEN→CLOSE/g)).toHaveLength(3);
    expect(html.match(/CLOSE→CLOSE/g)).toHaveLength(2);
  });
});
