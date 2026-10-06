/**
 * Risk (/risk): the active portfolio's VaR/ES across models, Euler split, backtests and
 * GARCH (the Portfolio Lab's former Risk tab), and the Monte Carlo atlas (P1, risk.mc_atlas).
 * `/risk?alpha=0.99&h=10` (the command line's PORT RISK) seeds the confidence and horizon.
 * Like Portfolio Lab, the analytics run on a committed copy of the portfolio.
 */
import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArtifactCell, EmptyState, Page } from "../components";
import { KINDS } from "../lib/artifacts";
import { usePortfolio } from "../lib/portfolio";
import type { PortfolioIn } from "../lib/types";
import { PortfolioStrip } from "./portfolio/PortfolioStrip";
import { RiskTab } from "./portfolio/RiskTab";
import { riskSeed } from "./risk/seed";
import "./portfolio/portfolio.css";

export default function Risk() {
  const { request } = usePortfolio();
  const [params] = useSearchParams();
  const seed = riskSeed(params);
  const [committed, setCommitted] = useState<PortfolioIn>(request);
  const dirty = useMemo(() => JSON.stringify(request) !== JSON.stringify(committed), [request, committed]);
  const run = useCallback(() => setCommitted(request), [request]);
  const empty = committed.holdings.length === 0;

  return (
    <Page
      title="Risk"
      meta={
        <>
          <span>VAR α {Number(seed.alpha) * 100}</span>
          <span>{seed.h}D</span>
        </>
      }
      actions={
        <Link to="/portfolio" className="oc-go">
          PORT →
        </Link>
      }
    >
      <PortfolioStrip dirty={dirty} onRun={run} />
      <div className="grid-3">
        <ArtifactCell title="ATLAS · MONTE CARLO VAR/ES" kind={KINDS.atlas} span="all" />
      </div>
      {empty ? (
        <EmptyState title="NO HOLDINGS">Add holdings in the editor above, then run.</EmptyState>
      ) : (
        <RiskTab key={`${seed.alpha}-${seed.h}`} req={committed} alpha={seed.alpha} horizon={seed.h} />
      )}
    </Page>
  );
}
