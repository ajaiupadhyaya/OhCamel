/**
 * Portfolio Lab (/portfolio) — the core page.
 *
 * Top: the active portfolio (usePortfolio) as a composition strip; the shared
 * <PortfolioBuilder/> opens in a drawer. Edits do not re-run the (heavy) analytics on every
 * keystroke: the tabs analyse a *committed* copy of the portfolio, and "Run analysis"
 * commits the current one. The first render commits immediately, so results appear on load.
 *
 * Tabs (URL ?tab=): Overview · Risk · Factors · Stress · Optimize.
 * Page-local code lives in ./portfolio/ (CSS prefix `pl-`).
 */
import { useCallback, useMemo, useState } from "react";
import { EmptyState, Page, Tabs, useTabParam } from "../components";
import { usePortfolio } from "../lib/portfolio";
import { useApiQuery } from "../lib/query";
import type { PortfolioIn } from "../lib/types";
import { FactorsTab } from "./portfolio/FactorsTab";
import { OptimizeTab } from "./portfolio/OptimizeTab";
import { OverviewTab } from "./portfolio/OverviewTab";
import { PortfolioStrip } from "./portfolio/PortfolioStrip";
import { RiskTab } from "./portfolio/RiskTab";
import { StressTab } from "./portfolio/StressTab";
import "./portfolio/portfolio.css";

type Tab = "overview" | "risk" | "factors" | "stress" | "optimize";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "risk", label: "Risk" },
  { id: "factors", label: "Factors" },
  { id: "stress", label: "Stress" },
  { id: "optimize", label: "Optimize" },
];

export default function PortfolioLab() {
  const { request } = usePortfolio();
  const [tab, setTab] = useTabParam<Tab>("tab", "overview");
  const [committed, setCommitted] = useState<PortfolioIn>(request);
  const dirty = useMemo(() => JSON.stringify(request) !== JSON.stringify(committed), [request, committed]);
  const run = useCallback(() => setCommitted(request), [request]);
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });
  const empty = committed.holdings.length === 0;
  const active = TABS.some((t) => t.id === tab) ? tab : "overview";

  return (
    <Page
      eyebrow="Analyze"
      title="Portfolio Lab"
      subtitle="Build or import a portfolio, then see how it has really behaved — performance, risk, factor exposures and stress — with every number explained."
      meta={
        health.data?.offline ? (
          <span className="badge unknown" title="The backend is running in offline mode: only committed daily prices (and a few FRED series) are available. Sources that need the network show a clear 'unavailable' state.">
            Offline dataset — committed daily prices only
          </span>
        ) : undefined
      }
    >
      <PortfolioStrip dirty={dirty} onRun={run} />

      <div className="pl-tabs">
        <Tabs items={TABS} value={active} onChange={setTab} />
      </div>

      {empty ? (
        <EmptyState icon="portfolio" title="Add holdings to analyse">
          Open the editor above and add tickers with weights, pick a preset universe, import a fund's 13F or paste a CSV. Then run the analysis.
        </EmptyState>
      ) : (
        <div className="pl-tab-body" key={active}>
          {active === "overview" && <OverviewTab req={committed} />}
          {active === "risk" && <RiskTab req={committed} />}
          {active === "factors" && <FactorsTab req={committed} />}
          {active === "stress" && <StressTab req={committed} />}
          {active === "optimize" && <OptimizeTab req={committed} />}
        </div>
      )}
    </Page>
  );
}
