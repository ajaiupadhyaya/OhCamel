/** Wording for the cross-section panel (POST /api/factors/cross-section). Pure, so it is tested without a DOM. */
import type { CrossSectionOut } from "./types";

const p3 = (p: number) => (p < 0.001 ? "<0.001" : p.toFixed(3));

/** The GRS line: statistic, p, and what it tested, with source and as-of. */
export function grsLine(d: CrossSectionOut): string {
  const g = d.grs;
  const verdict = g.p_value < 0.01 ? "rejected at 1%" : g.p_value < 0.05 ? "rejected at 5%" : "not rejected";
  return `GRS F(${g.n_assets}, ${g.n_obs - g.n_assets - g.n_factors}) = ${g.statistic.toFixed(2)} · p ${p3(g.p_value)} · all ${g.n_assets} alphas zero ${verdict} · ${g.n_obs} months to ${d.stats.grs_end.slice(0, 7)}`;
}

/** Source line shared by the premia table and the GRS line. */
export function sourceLine(d: CrossSectionOut): string {
  const legs = d.model.factors.map((f) => `${f.name} = ${f.long} − ${f.short ?? "T-BILL"}`).join(", ");
  return `Source: ETF total returns from the committed dataset, compounded monthly · test assets ${d.model.tickers.join(" ")} · factors ${legs} · betas from the ${d.stats.beta_window} months before each month · as of ${d.stats.end.slice(0, 7)}`;
}

/** Whether the Shanken correction changed a verdict at |t| = 2. */
export function shankenFlips(d: CrossSectionOut): string[] {
  return d.premia.filter((r) => Math.abs(r.t_nw) >= 2 && Math.abs(r.t_shanken) < 2).map((r) => r.term);
}
