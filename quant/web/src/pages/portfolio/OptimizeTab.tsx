/**
 * Optimize — hand the current holdings to the Optimizer (/optimize) as its universe, with
 * the current weights as the starting point. Shows capital vs risk share (from
 * POST /risk/decomposition) so the user sees what an optimizer would be fixing.
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Icon, Panel, Section } from "../../components";
import { Bars } from "./Bars";
import { fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { PortfolioIn } from "../../lib/types";
import { INFO } from "./info";
import { Reading } from "./shared";
import type { DecompositionOut } from "./types";

/** Deep link understood by the Optimizer page: ?tickers=A,B&weights=0.5,0.5&from=portfolio */
export function optimizerHref(req: PortfolioIn): string {
  const hs = req.holdings.filter((h) => h.weight !== 0);
  const p = new URLSearchParams();
  p.set("tickers", hs.map((h) => h.ticker).join(","));
  p.set("weights", hs.map((h) => +h.weight.toFixed(6)).join(","));
  if (req.start) p.set("start", req.start);
  if (req.end) p.set("end", req.end);
  p.set("from", "portfolio");
  return `/optimize?${p.toString()}`;
}

const METHODS = [
  { name: "Minimum variance", text: "The lowest-volatility mix of these same holdings — no return forecasts needed." },
  { name: "Risk parity", text: "Weights that make every holding contribute the same share of risk — the direct fix for a concentration like the one on the left." },
  { name: "Hierarchical risk parity", text: "Clusters similar assets first, then splits risk between clusters; robust when correlations are unstable." },
  { name: "Black–Litterman", text: "Starts from market-implied returns and tilts toward your own views with a stated confidence." },
];

export function OptimizeTab({ req }: { req: PortfolioIn }) {
  const body = useMemo(() => ({ ...req, alpha: 0.99, cov_method: "sample" as const }), [req]);
  const q = useApiPost<DecompositionOut>("/risk/decomposition", body);
  const href = optimizerHref(req);
  const n = req.holdings.length;
  return (
    <Section title="Improve the mix" description="The optimizer takes these holdings as its universe and today's weights as the starting point, so every suggestion is a change you can compare against what you hold now.">
      <div className="grid-3">
        <Panel<DecompositionOut> span={2} title="Capital share vs risk share" subtitle="Weight of each holding next to its share of 99% parametric VaR. A well-diversified portfolio has bars of similar height." info={INFO.component_var} query={q} skeletonHeight={300} notes={[]}>
          {(d) => {
            const rows = [...d.positions].sort((a, b) => b.pct_var - a.pct_var);
            const gap = rows.reduce((a, r) => a + Math.abs(r.pct_var - r.weight), 0) / 2;
            return (
              <>
                <Reading>
                  {fmtPct(gap, 0)} of the portfolio's risk would have to move to make each holding's risk share equal its capital share. Diversification ratio today: {d.totals.diversification_ratio.toFixed(2)}×.
                </Reading>
                <Bars
                  series={[
                    { name: "Weight", x: rows.map((r) => r.ticker), y: rows.map((r) => r.weight) },
                    { name: "Share of VaR", x: rows.map((r) => r.ticker), y: rows.map((r) => r.pct_var) },
                  ]}
                  yFormat="pct"
                  digits={1}
                  height={380}
                />
              </>
            );
          }}
        </Panel>
        <div className="pl-handoff">
          <div className="eyebrow">Send to the Optimizer</div>
          <h3 className="display pl-handoff-title">{n} holdings, current weights as the start</h3>
          <div className="pl-handoff-weights">
            {req.holdings.map((h) => (
              <span key={h.ticker} className="pl-chip num">
                {h.ticker} <span className="subtle">{fmtPct(h.weight, 1)}</span>
              </span>
            ))}
          </div>
          <Link to={href} className="btn btn-primary pl-handoff-btn">
            Open in Optimizer <Icon name="arrow-right" size={15} />
          </Link>
          <ul className="pl-methods">
            {METHODS.map((m) => (
              <li key={m.name}>
                <strong>{m.name}</strong>
                <span>{m.text}</span>
              </li>
            ))}
          </ul>
          <p className="subtle small">The same analysis window ({req.start ?? "full history"} – {req.end ?? "latest"}) is passed along. Nothing about your portfolio changes until you adopt a result there.</p>
        </div>
      </div>
    </Section>
  );
}
