import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import { FarmBoard, costsSource, sweepRows } from "./Farm";
import { ModelsReport } from "./Models";
import type { FarmBoardRow, FarmCellRow, GateRow, HoldoutRow, RegimeRow } from "./types";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

const row = (cell_id: string, verdict: string, dsr: number, detail: string): FarmBoardRow => {
  const [strategy, universe] = cell_id.split("@");
  return { cell_id, strategy, universe, verdict, detail, dsr_farm: dsr, psr: 0.6, oos_sharpe_ann: 0.4, holdout_return: -0.02, boot_lo5: -0.1, pbo: 0.62, spa_p: 0.4, n_combos: 16, selected_params: '{"months": 12}', data_asof: "2026-10-06", ran_at: "2026-10-06T23:10:00Z" };
};
const board = [row("xsmom@sectors", "PASS", 0.41, "all gates pass"), row("sma_trend@us_equity_indices", "FAIL", 0.12, "failed: dsr 0.12 (needs >= 0.3), holdout_positive -0.02 (needs > 0); PBO 0.62 (high)")];
const cells: FarmCellRow[] = [
  { cell_id: "xsmom@sectors", strategy: "xsmom", universe: "sectors", status: "ok", reason: null, ran_at: "2026-10-06T23:00:00Z", cost_curve: '[{"cost_bps": 0, "sharpe": 0.5, "cagr": 0.06}, {"cost_bps": 30, "sharpe": -0.1, "cagr": -0.01}]' },
  { cell_id: "sma_trend@us_equity_indices", strategy: "sma_trend", universe: "us_equity_indices", status: "ok", reason: null, ran_at: "2026-10-06T23:05:00Z" },
  { cell_id: "pairs@sectors", strategy: "pairs", universe: "sectors", status: "skipped", reason: "800 common live sessions (< 1512 for walk-forward)", ran_at: "2026-10-06T23:06:00Z" },
];
const regimes: RegimeRow[] = [{ cell_id: "xsmom@sectors", regime: "GFC", start: "2007-10-09", end: "2009-03-09", n: 354, total_return: -0.12, positive: false }];

describe("FarmBoard (P4 leaderboard)", () => {
  const html = render(<FarmBoard board={board} cells={cells} regimes={regimes} names={{ xsmom: "Cross-sectional momentum" }} />);

  it("counts the failures first and largest", () => {
    const fail = html.indexOf("sl-count-fail");
    expect(fail).toBeGreaterThan(-1);
    expect(fail).toBeLessThan(html.indexOf(">PASS<"));
    expect(html).toMatch(/sl-count-item sl-count-big sl-count-fail/);
  });

  it("leads every leaderboard row with its verdict; FAIL in signal", () => {
    const head = html.slice(html.indexOf("<thead"), html.indexOf("</thead>"));
    expect(head.indexOf("Verdict")).toBeLessThan(head.indexOf("Strategy"));
    expect(html).toMatch(/class="sl-v sl-v-fail"[^>]*>FAIL</);
    expect(html).toContain("Cross-sectional momentum");
  });

  it("names the failed gates by code and lists skipped cells with their reason", () => {
    expect(html).toContain("DSR · HOLD");
    expect(html).toContain("PAIRS");
    expect(html).toContain("1512 for walk-forward");
    expect(html).toContain("SHORT HISTORY");
  });

  it("shows the selected cell's regimes and its cost ladder", () => {
    expect(html).toContain("GFC");
    expect(html).toMatch(/>30</);
  });

  it("carries turnover, capacity and the half-spread sweep from the cost file, absent when a row has none", () => {
    const priced = { ...board[0], turnover: 4.2, capacity_usd: 12_500_000, sr_at_0x: 0.61, sr_at_1x: 0.55, sr_at_3x: 0.41, sr_at_6x: 0.2 };
    const out = render(<FarmBoard board={[priced, board[1]]} cells={cells} regimes={regimes} names={{}} />);
    const head = out.slice(out.indexOf("<thead"), out.indexOf("</thead>"));
    expect(head).toContain("Turnover");
    expect(head).toContain("Capacity");
    expect(out).toContain("SPREAD SWEEP");
    expect(out).toContain("6×");
    expect(out).toContain("0.20");
    expect(html).toContain("NO SPREAD SWEEP"); // the fixture rows predate the cost file
  });

  it("reads the sweep's multiples from the row, so a changed sweep in the cost file renders, never blanks", () => {
    expect(sweepRows({ ...board[0], sr_at_0x: 0.5, sr_at_2x: 0.3, sr_at_10x: -0.2 }).map((r) => [r.mult, r.sharpe])).toEqual([[0, 0.5], [2, 0.3], [10, -0.2]]);
    const out = render(<FarmBoard board={[{ ...board[0], sr_at_0x: 0.5, sr_at_2x: 0.31 }]} cells={cells} regimes={regimes} names={{}} />);
    expect(out).toContain("2×");
    expect(out).toContain("0.31");
  });

  it("says the tiers are provisional only while the artifact's provenance counts provisional tiers", () => {
    const prov = (n: number) => ({ provenance: [{ source: "costs", fetched_at: "", path: "research/config/costs.yaml", version: "1.0.0", tiers: 42, provisional_tiers: n }] });
    expect(costsSource(prov(42))).toContain("42 of 42 tiers provisional");
    expect(costsSource(prov(0))).toContain("every tier measured");
    expect(costsSource(prov(0))).not.toContain("provisional");
    expect(costsSource(null)).toBe("research/config/costs.yaml");
  });
});

const gates: GateRow[] = [
  { gate: "holdout_positive", value: 0.031, rule: "> 0", passed: true, note: null },
  { gate: "dsr", value: 0.12, rule: ">= 0.3", passed: false, note: null },
  { gate: "pbo", value: 0.4, rule: "reported", passed: null, note: null },
];
const holdout = {
  holdout_start: "2022-01-31", holdout_end: "2026-09-30", selected_config: 3, params: "{'num_leaves': 15}", verdict: "FAIL", verdict_detail: "failed: dsr 0.12 (needs >= 0.3)",
  net_sharpe: 0.35, composite_net_sharpe: 0.41, total_return: 0.031, max_drawdown: -0.18, dsr: 0.12, n_trials: 40, psr: 0.66, boot_lo5: -0.2, pbo: 0.4,
  ic_mean: 0.02, ic_hac_t: 1.1, rank_ic_mean: 0.03, rank_ic_hac_t: 1.4, annual_turnover: 6.2, capacity_usd: 4.1e8, methodology_version: 1, config_hash: "abc", evaluated_at: "2026-10-01",
} as HoldoutRow;

describe("ModelsReport (P5 EXP-Q01)", () => {
  it("before the holdout is evaluated it says so and shows no figures", () => {
    const html = render(<ModelsReport holdout={null} gates={[]} costs={[]} regimes={[]} deciles={[]} returns={[]} scores={[]} />);
    expect(html).toContain("HOLDOUT NOT EVALUATED");
    expect(html).not.toContain("RANK IC");
  });

  it("shows the gates with FAIL in signal, the rank IC with its HAC t, and the composite beside the model", () => {
    const html = render(<ModelsReport holdout={holdout} gates={gates} costs={[{ cost_bps: 0, model_sharpe: 0.4, composite_sharpe: 0.45, model_total: 0.05 }]} regimes={[]} deciles={[{ decile: 1, mean_next_month_return: -0.01 }, { decile: 10, mean_next_month_return: 0.012 }]} returns={[]} scores={[{ ticker: "XLK", score: 0.8, side: "long", rank: 1 }]} />);
    expect(html).toMatch(/class="sl-gate-fail"[^>]*>FAIL</);
    expect(html).toContain("RANK IC");
    expect(html).toContain("t 1.40");
    expect(html).toContain("LIN 0.41");
    expect(html).toContain("XLK");
    expect(html).toContain("N 40");
  });
});
