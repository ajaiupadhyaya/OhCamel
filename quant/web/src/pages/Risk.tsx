/**
 * Risk (/risk): the Monte Carlo atlas (P1, risk.mc_atlas) over the reference books, then the
 * active portfolio's VaR/ES across nine models, Euler split, backtests and GARCH.
 * `/risk?alpha=0.99&h=10` (the command line's PORT RISK) seeds the confidence and horizon.
 * The portfolio analytics run on a committed copy of the portfolio (RUN in the strip).
 * Page-local code in ./risk/; shared portfolio pieces (strip, KV, ShareRows) in ./portfolio/.
 */
import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Page, Section } from "../components";
import { Absent } from "../design";
import { usePortfolio } from "../lib/portfolio";
import { useApiQuery } from "../lib/query";
import type { PortfolioIn } from "../lib/types";
import { PortfolioStrip } from "./portfolio/PortfolioStrip";
import { Atlas } from "./risk/AtlasCells";
import { RiskSections } from "./risk/RiskSections";
import { riskSeed } from "./risk/seed";
import "./portfolio/portfolio.css";

export default function Risk() {
  const { request } = usePortfolio();
  const [params] = useSearchParams();
  const seed = riskSeed(params);
  const [committed, setCommitted] = useState<PortfolioIn>(request);
  const dirty = useMemo(() => JSON.stringify(request) !== JSON.stringify(committed), [request, committed]);
  const run = useCallback(() => setCommitted(request), [request]);
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });
  const empty = committed.holdings.length === 0;

  return (
    <Page
      title="Risk"
      meta={
        <>
          <span>VAR {Number(seed.alpha) * 100}</span>
          <span>{seed.h}D</span>
          {health.data?.offline && <span>OFFLINE DATASET</span>}
        </>
      }
      actions={
        <Link to="/portfolio" className="oc-go">
          PORT →
        </Link>
      }
    >
      <Section title="Atlas · P1">
        <Atlas seedAlpha={seed.alpha} seedH={seed.h} />
      </Section>
      <PortfolioStrip dirty={dirty} onRun={run} />
      {empty ? <Absent reason="NO HOLDINGS" source="EDIT · ADD HOLDINGS · RUN" /> : <RiskSections key={`${seed.alpha}-${seed.h}`} req={committed} alpha={seed.alpha} horizon={seed.h} />}
    </Page>
  );
}
