/**
 * Optimize — hand the current holdings to the Optimizer (/optimize) as its universe, current
 * weights as the start. Shows capital against 99% VaR share (POST /risk/decomposition): what
 * an optimizer would be fixing. Method descriptions live in Methodology.
 */
import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Panel, Section } from "../../components";
import { fmtMultiple, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { PortfolioIn } from "../../lib/types";
import { INFO } from "./info";
import { KV, ShareRows } from "./shared";
import type { DecompositionOut } from "./types";

/** Deep link understood by the Optimizer page: ?tickers=A,B&weights=0.5,0.5&from=portfolio */
export function optimizerHref(req: PortfolioIn): string {
  const hs = req.holdings.filter((h) => h.weight !== 0);
  const p = new URLSearchParams();
  p.set("tickers", hs.map((h) => h.ticker).join(","));
  p.set("weights", hs.map((h) => Math.round(h.weight * 1e6) / 1e6).join(","));
  if (req.start) p.set("start", req.start);
  if (req.end) p.set("end", req.end);
  p.set("from", "portfolio");
  return `/optimize?${p.toString()}`;
}

export function OptimizeTab({ req }: { req: PortfolioIn }) {
  const body = useMemo(() => ({ ...req, alpha: 0.99, cov_method: "sample" as const }), [req]);
  const q = useApiPost<DecompositionOut>("/risk/decomposition", body);
  const href = optimizerHref(req);
  return (
    <Section
      title="Optimize"
      actions={
        <Link to={href} className="oc-go">
          OPT · {req.holdings.length} HOLDINGS →
        </Link>
      }
    >
      <div className="grid-3">
        <Panel<DecompositionOut> span={2} title="Capital · VaR 99 share" info={INFO.component_var} query={q} skeletonHeight={300} notes={[]} flush>
          {(d) => <ShareRows rows={d.positions.map((p) => ({ ticker: p.ticker, weight: p.weight, share: p.pct_var }))} riskLabel="VAR 99" />}
        </Panel>
        <Panel<DecompositionOut> title="Concentration" query={q} skeletonHeight={160} notes={[]}>
          {(d) => {
            const gap = d.positions.reduce((a, r) => a + Math.abs(r.pct_var - r.weight), 0) / 2;
            return (
              <KV
                rows={[
                  { label: "Risk to move", value: fmtPct(gap, 1), info: { text: "Half the summed |VaR share − weight|: risk that must move for shares to equal weights." } },
                  { label: "Div ratio", value: fmtMultiple(d.totals.diversification_ratio, 2), info: INFO.diversification_ratio },
                  { label: "Window", value: `${req.start ?? "FULL"} – ${req.end ?? "LATEST"}` },
                ]}
              />
            );
          }}
        </Panel>
      </div>
    </Section>
  );
}
