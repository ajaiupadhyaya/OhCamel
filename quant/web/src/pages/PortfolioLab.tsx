/**
 * Portfolio (/portfolio): the active portfolio (usePortfolio) as a ruled strip with the shared
 * <PortfolioBuilder/> below it. The tabs analyse a *committed* copy; RUN commits the current one
 * (the first render commits immediately).
 *
 * Tabs (URL ?tab=): OVERVIEW · FACTORS · STRESS · COV (the P3 covariance league, an artifact)
 * · OPTIMIZE. Risk has its own route, /risk; the old ?tab=risk bookmark redirects there.
 * Page-local code lives in ./portfolio/ (CSS prefix `pl-`).
 */
import { useCallback, useMemo, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { Page, Tabs, useTabParam } from "../components";
import { Absent } from "../design";
import { usePortfolio } from "../lib/portfolio";
import { useApiQuery } from "../lib/query";
import type { PortfolioIn } from "../lib/types";
import { CovLeague } from "./portfolio/CovLeague";
import { FactorsTab } from "./portfolio/FactorsTab";
import { OptimizeTab } from "./portfolio/OptimizeTab";
import { OverviewTab } from "./portfolio/OverviewTab";
import { PortfolioStrip } from "./portfolio/PortfolioStrip";
import { StressTab } from "./portfolio/StressTab";
import "./portfolio/portfolio.css";

type Tab = "overview" | "factors" | "stress" | "cov" | "optimize";
const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "factors", label: "Factors" },
  { id: "stress", label: "Stress" },
  { id: "cov", label: "Cov" },
  { id: "optimize", label: "Optimize" },
];

export default function PortfolioLab() {
  const { request } = usePortfolio();
  const [tab, setTab] = useTabParam<Tab | "risk">("tab", "overview");
  const [committed, setCommitted] = useState<PortfolioIn>(request);
  const dirty = useMemo(() => JSON.stringify(request) !== JSON.stringify(committed), [request, committed]);
  const run = useCallback(() => setCommitted(request), [request]);
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });
  const empty = committed.holdings.length === 0;
  const active = TABS.some((t) => t.id === tab) ? (tab as Tab) : "overview";

  if (tab === "risk") return <Navigate to="/risk" replace />;
  return (
    <Page
      title="Portfolio"
      meta={health.data?.offline ? <span>OFFLINE DATASET</span> : undefined}
      actions={
        <Link to="/risk" className="oc-go">
          RISK →
        </Link>
      }
    >
      <PortfolioStrip dirty={dirty} onRun={run} />

      <div className="pl-tabs">
        <Tabs items={TABS} value={active} onChange={setTab} />
      </div>

      {active === "cov" ? (
        <CovLeague />
      ) : empty ? (
        <Absent reason="NO HOLDINGS" source="EDIT · ADD HOLDINGS · RUN" />
      ) : (
        <div className="pl-tab-body" key={active}>
          {active === "overview" && <OverviewTab req={committed} />}
          {active === "factors" && <FactorsTab req={committed} />}
          {active === "stress" && <StressTab req={committed} />}
          {active === "optimize" && <OptimizeTab req={committed} />}
        </div>
      )}
    </Page>
  );
}
