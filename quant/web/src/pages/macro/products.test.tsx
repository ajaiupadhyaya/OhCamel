import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../charts/theme", async (orig) => ({
  ...(await orig<typeof import("../../charts/theme")>()),
  useChartTheme: () => new Proxy({}, { get: () => "" }),
}));
import { ThemeProvider } from "../../lib/theme";
import { probRows, q02Holdout } from "./derive";
import { Q02Report, fitOf, gateOf } from "./RegimesTab";

function render(node: ReactNode) {
  return renderToStaticMarkup(
    <ThemeProvider>
      <MemoryRouter>{node}</MemoryRouter>
    </ThemeProvider>,
  );
}

const probs = probRows([
  { date: "2026-09-18", p_high: 0.12, p_high_smoothed_history: 0.1 },
  { date: "2026-09-25", p_high: 0.31, p_high_smoothed_history: 0.42 },
  { date: "2026-10-02", p_high: 0.64, p_high_smoothed_history: 0.71 },
]);
const fits = [
  { refit: "2026-06-26", k: 2, loglik: -1200.5, bic: 2511.2, weeks: 1500, converged: true },
  { refit: "2026-09-25", k: 3, loglik: -1180.1, bic: 2490.7, weeks: 1513, converged: false },
].map(fitOf);
const gates = [
  { gate: "selection_hac_t_on_p", value: 1.4, rule: "> 2", passed: false },
  { gate: "holdout_dm_one_sided_p", value: 0.31, rule: "< 0.05 and lower QLIKE", passed: false },
  { gate: "incremental_r2_holdout", value: 0.004, rule: "reported", passed: null },
].map(gateOf);
const holdout = q02Holdout([
  { selection_weeks: 1000, boundary_weeks_purged: 4, holdout_weeks: 230, hac_t_p: 1.4, hac_lags: 9, dm_stat: 0.5, dm_pvalue: 0.31, qlike_ewma: -3.21, qlike_ewma_p: -3.2, oos_r2_ewma: 0.31, oos_r2_ewma_p: 0.314, holdout_start: "2022-01-07", holdout_end: "2026-05-29", methodology_version: 1 },
]);

describe("Q02Report (P6 regime.hmm)", () => {
  const html = render(<Q02Report probs={probs} fits={fits} holdout={holdout} gates={gates} evaluated />);

  it("leads with the latest filtered P(high vol), and labels the smoothed one as hindsight", () => {
    expect(html).toContain("64%");
    expect(html).toContain("FILTERED · 2 OCT 26");
    expect(html).toContain("71%");
    expect(html).toContain("HINDSIGHT");
  });
  it("shows the holdout window, purged weeks and the gate table with FAIL in signal", () => {
    expect(html).toContain("7 JAN 2022");
    expect(html).toContain("4 PURGED");
    expect(html).toContain("HAC t ON P · SELECTION");
    expect(html).toMatch(/class="loss">FAIL</);
    expect(html).toContain("REPORTED");
  });
  it("lists refits newest first and marks a non-converged EM", () => {
    expect(html.indexOf("25 SEP 26")).toBeLessThan(html.indexOf("26 JUN 26"));
    expect(html).toContain("NOT CONVERGED");
  });
  it("without a holdout says so instead of showing test figures", () => {
    const h = render(<Q02Report probs={probs} fits={[]} holdout={null} gates={[]} evaluated={false} />);
    expect(h).toContain("HOLDOUT NOT EVALUATED");
    expect(h).toContain("NO GATE ROWS");
    expect(h).not.toContain("DM p");
  });
  it("no probability rows: INSUFFICIENT DATA, no chart", () => {
    const h = render(<Q02Report probs={[]} fits={[]} holdout={null} gates={[]} evaluated={false} />);
    expect(h).toContain("NO PROBABILITY ROWS");
    expect(h).toContain("NO FILTERED WEEK");
  });
});
