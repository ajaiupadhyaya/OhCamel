/**
 * Research (/research): LAB, FARM and MODELS.
 *
 * LAB runs one literature rule at a time on real prices
 * (quant/src/ohcamel_quant/api/routers/backtest.py):
 *   GET  /api/backtest/strategies     catalog: parameter schemas, citations, default universes
 *   POST /api/backtest/run            one backtest (auto-runs once with the paper defaults)
 *   POST /api/backtest/sweep          parameter grid + PBO / DSR / SPA        (SWEEP)
 *   POST /api/backtest/walkforward    rolling re-optimisation                  (WALK-FWD)
 *   POST /api/backtest/costs          Sharpe / CAGR vs cost, break-even        (COSTS)
 *   GET  /api/market/overview         which tickers any configured source can serve
 * Inputs are a draft; the heavy POSTs run on explicit buttons.
 * FARM reads farm.sweep (P4) and MODELS reads models.xs_lgbm (P5, EXP-Q01) through
 * GET /api/artifacts/{kind}/latest; before Lane M's first run they read NOT YET RUN.
 * Every result leads with its verdict. Page-local components live in ./research/ (`sl-`).
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Page, Skeleton, Tabs, useTabParam } from "../components";
import { Absent } from "../design";
import type { ApiError } from "../lib/api";
import { fmtDate } from "../lib/format";
import { usePortfolio } from "../lib/portfolio";
import { useApiPost, useApiQuery } from "../lib/query";
import { BacktestTab } from "./research/BacktestTab";
import { Catalog, StrategyFoot } from "./research/Catalog";
import { CostsTab } from "./research/CostsTab";
import { DEFAULT_STRATEGY, baseBody, initialConfig, type BaseBody, type LabConfig } from "./research/config";
import { FarmView } from "./research/Farm";
import { ModelsView } from "./research/Models";
import { SetupRail } from "./research/Setup";
import { useAvailability } from "./research/shared";
import { SweepTab } from "./research/SweepTab";
import type { Catalog as CatalogT, RunOut } from "./research/types";
import { WalkForwardTab } from "./research/WalkForwardTab";
import "./research/research.css";

type View = "lab" | "farm" | "models";
type Tab = "backtest" | "sweep" | "walkforward" | "costs";

const VIEWS = [
  { id: "lab" as const, label: "Lab" },
  { id: "farm" as const, label: "Farm · P4" },
  { id: "models" as const, label: "Models · P5" },
];

export default function StrategyLab() {
  const [view, setView] = useTabParam<View>("view", "lab");
  const catalog = useApiQuery<CatalogT>("/backtest/strategies", undefined, { staleTime: Infinity });
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });
  return (
    <Page
      title="Strategy Lab"
      meta={
        <>
          {health.data?.offline && <span title="Offline mode: committed daily data only">OFFLINE DATASET</span>}
          {catalog.data && <span className="num">{catalog.data.strategies.length} STRATEGIES</span>}
        </>
      }
    >
      <div className="sl-sections">
        <Tabs<View> items={VIEWS} value={view} onChange={setView} />
      </div>
      {view === "farm" ? <FarmView /> : view === "models" ? <ModelsView /> : <Lab catalog={catalog} />}
    </Page>
  );
}

function Lab({ catalog }: { catalog: UseQueryResult<CatalogT, ApiError> }) {
  const { portfolio } = usePortfolio();
  const [sKey, setSKey] = useTabParam<string>("s", DEFAULT_STRATEGY);
  const [tab, setTab] = useTabParam<Tab>("tab", "backtest");

  const strategies = useMemo(() => catalog.data?.strategies ?? [], [catalog.data]);
  const spec = strategies.find((s) => s.key === sKey) ?? strategies.find((s) => s.key === DEFAULT_STRATEGY) ?? strategies[0];

  // ---- draft inputs (reset to the paper's defaults whenever the strategy changes)
  const [draft, setDraft] = useState<LabConfig | null>(null);
  const [pendingAuto, setPendingAuto] = useState(true);
  useEffect(() => {
    if (!spec) return;
    setDraft((prev) => (prev?.strategy === spec.key ? prev : initialConfig(spec, prev ?? { benchmark: portfolio.benchmark || "SPY" })));
    setPendingAuto(true);
  }, [spec?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = useCallback((p: Partial<LabConfig>) => setDraft((c) => (c ? { ...c, ...p } : c)), []);
  const cfg = draft && spec && draft.strategy === spec.key ? draft : null;

  // ---- which tickers can actually be served
  const avail = useAvailability(cfg ? [...cfg.tickers, cfg.benchmark] : []);
  const usable = useMemo(() => (cfg ? cfg.tickers.filter((t) => avail.status(t) === "ok") : []), [cfg, avail]);
  const settled = !!cfg && !avail.loading && [...cfg.tickers, cfg.benchmark].every((t) => avail.status(t) !== "pending");
  const runnable = settled && !!spec && usable.length >= spec.min_assets && avail.status(cfg!.benchmark) === "ok";
  const current = useMemo<BaseBody | null>(() => (cfg && spec ? baseBody(cfg, spec, usable) : null), [cfg, spec, usable]);

  // ---- committed run
  const [committed, setCommitted] = useState<BaseBody | null>(null);
  useEffect(() => {
    if (!pendingAuto || !settled) return;
    if (runnable && current) setCommitted(current);
    setPendingAuto(false);
  }, [pendingAuto, settled, runnable, current]);
  const base = committed && spec && committed.strategy === spec.key ? committed : null;
  const run = useApiPost<RunOut>("/backtest/run", base, { enabled: !!base });
  const dirty = !!current && JSON.stringify(current) !== JSON.stringify(base);
  const doRun = () => current && setCommitted(current);

  if (catalog.isError) return <Absent reason="CATALOG UNAVAILABLE" source="GET /api/backtest/strategies" />;
  if (!catalog.data || !spec)
    return (
      <div className="sl-layout">
        <Skeleton height={640} />
        <Skeleton height={640} />
      </div>
    );

  return (
    <div className="sl-layout">
      {cfg ? (
        <SetupRail spec={spec} catalog={catalog.data} cfg={cfg} set={set} avail={avail} portfolio={portfolio} onReset={() => setDraft(initialConfig(spec, { benchmark: cfg.benchmark }))} />
      ) : (
        <Skeleton height={480} />
      )}
      <div className="sl-main">
        <div>
          <Catalog strategies={strategies} selected={spec.key} onSelect={(k) => setSKey(k)} />
          <StrategyFoot spec={spec} />
          <RunBar dirty={dirty} runnable={runnable} settled={settled} fetching={run.isFetching} hasRun={!!base} onRun={doRun} usable={usable.length} window={run.data?.window} failed={run.isError} />
        </div>
        <div className="sl-tabs">
          <Tabs<Tab>
            items={[
              { id: "backtest", label: "Backtest" },
              { id: "sweep", label: "Sweep · PBO" },
              { id: "walkforward", label: "Walk-fwd" },
              { id: "costs", label: "Costs" },
            ]}
            value={tab}
            onChange={setTab}
          />
        </div>
        {!base && !run.data && settled && !runnable ? (
          <Absent reason={`MIN ${spec.min_assets} ASSET${spec.min_assets > 1 ? "S" : ""} WITH DATA · BENCHMARK WITH DATA`} source="UNIVERSE · WINDOW" />
        ) : tab === "backtest" ? (
          <BacktestTab q={run} />
        ) : tab === "sweep" ? (
          <SweepTab key={spec.key} spec={spec} base={base} maxCombos={catalog.data.caps.grid_combinations} />
        ) : tab === "walkforward" ? (
          <WalkForwardTab key={spec.key} spec={spec} base={base} maxCombos={catalog.data.caps.grid_combinations} />
        ) : (
          <CostsTab key={spec.key} base={base} cap={catalog.data.caps.cost_points} />
        )}
      </div>
    </div>
  );
}

function RunBar({ dirty, runnable, settled, fetching, hasRun, onRun, usable, window, failed }: { dirty: boolean; runnable: boolean; settled: boolean; fetching: boolean; hasRun: boolean; onRun: () => void; usable: number; window?: RunOut["window"]; failed: boolean }) {
  let status: ReactNode;
  if (!settled) status = <span className="subtle">CHECKING DATA</span>;
  else if (!runnable) status = <span className="loss">NO RUNNABLE UNIVERSE</span>;
  else if (fetching) status = <span className="subtle">RUNNING · {usable} ASSETS</span>;
  else if (dirty && hasRun) status = <span>INPUTS CHANGED · NOT RUN</span>;
  else if (failed) status = <span className="loss">LAST RUN FAILED</span>;
  else if (hasRun && window) status = <span>{usable} ASSETS · LIVE {fmtDate(window.live_start)} – {fmtDate(window.end)}</span>;
  else status = <span className="subtle">READY</span>;
  return (
    <div className="sl-runbar num">
      <button type="button" className={`btn btn-sm ${dirty || failed ? "btn-primary" : ""}`} onClick={onRun} disabled={!runnable || fetching || (!dirty && !failed)}>
        {fetching ? "RUNNING" : "RUN"}
      </button>
      {status}
    </div>
  );
}
