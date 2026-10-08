import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import { AtlasReport, FarmReport, ModelsReport, RegimeReport } from "./Products";
import type { FarmBoardRow, FarmCellRow, HoldoutRow } from "../research/types";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

describe("AtlasReport (risk.mc_atlas · summary, as Lane M writes it)", () => {
  const rows = [
    { book: "core", method: "fhs", horizon: 1, alpha: 0.99, var: 0.012753, es: 0.015411, n_paths: null, notional: 100000, var_usd: 1275.3, es_usd: 1541.1 },
    { book: "core", method: "copula_t", horizon: 1, alpha: 0.99, var: 0.014, es: 0.0187, n_paths: 100000, notional: 100000, var_usd: 1400, es_usd: 1870 },
  ];
  const html = render(<AtlasReport rows={rows} source="risk.mc_atlas/x/summary" />);
  it("shows FHS and t-copula VaR and ES at 99 · 1D with dollars", () => {
    expect(html).toContain("FHS VAR");
    expect(html).toContain("T-COP ES");
    expect(html).toContain("1.28%");
    expect(html).toContain("1.87%");
    expect(html).toContain("$1,275");
    expect(html).toContain("BOOK CORE");
  });
  it("an unknown shape says so", () => {
    expect(render(<AtlasReport rows={[{ x: 1 }]} source="s" />)).toContain("NO RUN AT 99 · 1D");
  });
});

const row = (cell_id: string, verdict: string, dsr: number): FarmBoardRow => {
  const [strategy, universe] = cell_id.split("@");
  return { cell_id, strategy, universe, verdict, detail: null, dsr_farm: dsr, psr: null, oos_sharpe_ann: null, holdout_return: null, boot_lo5: null, pbo: null, spa_p: null, n_combos: null, selected_params: null, data_asof: null, ran_at: null };
};

describe("FarmReport (farm.sweep · leaderboard)", () => {
  const board = [row("a@sectors", "FAIL", 0.11), row("b@sectors", "FAIL", 0.21), row("c@sectors", "PASS", 0.44), row("d@sectors", "FAIL", 0.02)];
  const cells: FarmCellRow[] = [{ cell_id: "e@sectors", strategy: "e", universe: "sectors", status: "skipped", reason: "short", ran_at: null }];
  const html = render(<FarmReport board={board} cells={cells} names={{ c: "Cross-sectional momentum" }} />);
  it("the fail count comes first and largest", () => {
    expect(html.indexOf("fp-farm-fail")).toBeLessThan(html.indexOf(">PASS<"));
    expect(html).toMatch(/fp-farm-fail num "?[^>]*>3</);
    expect(html).toContain("SKIPPED");
  });
  it("three rows by DSR, verdict first; FAIL in signal", () => {
    const head = html.slice(html.indexOf("<thead"), html.indexOf("</thead>"));
    expect(head.indexOf("VERDICT")).toBeLessThan(head.indexOf("STRATEGY"));
    expect(html).toContain("CROSS-SECTIONAL MOMENTUM");
    expect(html).not.toContain(">D<");
    expect(html).toMatch(/fp-v fp-v-fail/);
  });
  it("an empty board shows the counts only, and zero failures are not red", () => {
    const empty = render(<FarmReport board={[]} cells={cells} names={{}} />);
    expect(empty).not.toContain("<table");
    expect(empty).toContain("fp-farm-zero");
  });
});

describe("ModelsReport (models.xs_lgbm · holdout)", () => {
  it("rank IC and its HAC t", () => {
    const h = { holdout_start: "2022-01-31", holdout_end: "2026-08-31", rank_ic_mean: 0.0312, rank_ic_hac_t: 1.84, net_sharpe: 0.41, composite_net_sharpe: 0.38 } as HoldoutRow;
    const html = render(<ModelsReport h={h} source="s" />);
    expect(html).toContain("RANK IC");
    expect(html).toContain("0.031");
    expect(html).toContain("1.84");
  });
  it("no row says so", () => expect(render(<ModelsReport h={null} source="s" />)).toContain("HOLDOUT ROW EMPTY"));
});

describe("RegimeReport (regime.hmm · probs)", () => {
  it("the latest filtered probability and its state", () => {
    const html = render(<RegimeReport rows={[{ date: "2026-09-25", p_high: 0.3 }, { date: "2026-10-02", p_high: 0.62 }]} source="s" />);
    expect(html).toContain("62%");
    expect(html).toContain("HIGH VOL");
    expect(html).toContain("02 OCT 2026");
  });
  it("no filtered week says so", () => expect(render(<RegimeReport rows={[]} source="s" />)).toContain("NO FILTERED WEEK"));
});
