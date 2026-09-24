/**
 * Strategy Lab (/research) — run rules from the academic literature on real prices, then
 * find out how much of the result is luck.
 *
 * Data (quant/src/ohcamel_quant/api/routers/backtest.py):
 *   GET  /api/backtest/strategies     catalog: parameter schemas, citations, default universes
 *   POST /api/backtest/run            one backtest (auto-runs with the paper defaults)
 *   POST /api/backtest/sweep          parameter grid + PBO / DSR / SPA        (Sweep tab)
 *   POST /api/backtest/walkforward    rolling re-optimisation                  (Walk-forward tab)
 *   POST /api/backtest/costs          Sharpe / CAGR vs cost, break-even        (Costs tab)
 *   GET  /api/market/universes, /api/market/overview  — universe names and which tickers any
 *        configured source can serve (offline: the committed fixtures only).
 * Inputs are a draft; the heavy POSTs run on explicit buttons. Selecting a strategy runs it
 * once with its defaults so results are on screen immediately.
 * Page-local components live in ./research/ (CSS prefix `sl-`).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Page, Panel, Skeleton, Tabs, useTabParam } from "../components";
import { Icon } from "../components/Icon";
import { fmtDate } from "../lib/format";
import { useUniverses } from "../lib/market";
import { usePortfolio } from "../lib/portfolio";
import { useApiPost, useApiQuery } from "../lib/query";
import { BacktestTab } from "./research/BacktestTab";
import { Catalog } from "./research/Catalog";
import { CostsTab } from "./research/CostsTab";
import { DEFAULT_STRATEGY, baseBody, initialConfig, type BaseBody, type LabConfig } from "./research/config";
import { Guardrails } from "./research/Guardrails";
import { CATEGORY_LABEL } from "./research/info";
import { Setup } from "./research/Setup";
import { useAvailability } from "./research/shared";
import { SweepTab } from "./research/SweepTab";
import type { Catalog as CatalogT, RunOut, StrategySpec, SweepOut } from "./research/types";
import { WalkForwardTab } from "./research/WalkForwardTab";
import "./research/research.css";

type Tab = "backtest" | "sweep" | "walkforward" | "costs";

export default function StrategyLab() {
  const catalog = useApiQuery<CatalogT>("/backtest/strategies", undefined, { staleTime: Infinity });
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });
  const universes = useUniverses();
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

  const [sweepData, setSweepData] = useState<SweepOut | undefined>(undefined);
  useEffect(() => setSweepData(undefined), [spec?.key]);

  const universeLabel = useCallback(
    (tickers: string[]) => {
      const list = universes.data ?? [];
      const labels: string[] = [];
      for (const t of tickers) {
        const u = list.find((x) => x.tickers.includes(t));
        const l = u?.label ?? null;
        if (l && !labels.includes(l)) labels.push(l);
      }
      if (!labels.length) return null;
      if (labels.length === 1) return labels[0];
      return labels.length <= 3 ? labels.join(" · ") : `Multi-asset (${labels.length} groups)`;
    },
    [universes.data],
  );

  const openTab = (t: Tab) => {
    setTab(t);
    requestAnimationFrame(() => document.getElementById("sl-results")?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  return (
    <Page
      eyebrow="Research · Backtesting"
      title="Strategy Lab"
      subtitle="Run rules from the academic literature on real prices, with costs and honest execution — then find out how much of the result is luck."
      meta={
        <>
          {health.data?.offline && (
            <span className="badge unknown" title="The backend is running offline: only the committed daily price history is available, so tickers without a fixture are left out of each run.">
              Offline dataset — committed prices only
            </span>
          )}
          {catalog.data && <span className="subtle small">{catalog.data.strategies.length} strategies · {catalog.data.method.model.toLowerCase()} · decide at the close, trade the next</span>}
        </>
      }
    >
      {catalog.isError ? (
        <Panel title="Strategy catalog" error={catalog.error} onRetry={() => catalog.refetch()} />
      ) : !catalog.data || !spec ? (
        <div className="sl-layout">
          <Skeleton height={640} />
          <div className="stack">
            <Skeleton height={180} />
            <Skeleton height={360} />
          </div>
        </div>
      ) : (
        <div className="sl-layout">
          <Catalog strategies={strategies} selected={spec.key} onSelect={(k) => setSKey(k)} universeLabel={universeLabel} />
          <div className="sl-main">
            <StrategyHero spec={spec} />
            {cfg ? (
              <Setup
                spec={spec}
                catalog={catalog.data}
                cfg={cfg}
                set={set}
                avail={avail}
                portfolio={portfolio}
                onReset={() => setDraft(initialConfig(spec, { benchmark: cfg.benchmark }))}
                footer={<RunBar dirty={dirty} runnable={runnable} settled={settled} fetching={run.isFetching} hasRun={!!base} onRun={doRun} usable={usable.length} window={run.data?.window} failed={run.isError} />}
              />
            ) : (
              <Skeleton height={360} />
            )}
            <Guardrails sweep={sweepData} onOpen={openTab} />
            <div id="sl-results" className="sl-results">
              <Tabs<Tab>
                items={[
                  { id: "backtest", label: "Backtest" },
                  { id: "sweep", label: "Sweep & overfitting" },
                  { id: "walkforward", label: "Walk-forward" },
                  { id: "costs", label: "Costs" },
                ]}
                value={tab}
                onChange={setTab}
              />
              {!base && !run.data && settled && !runnable ? (
                <div className="oc-panel sl-empty">
                  <Icon name="alert" size={20} />
                  <div>
                    <div className="sl-empty-title">Nothing to run yet.</div>
                    <p className="subtle small">{spec.name} needs at least {spec.min_assets} asset{spec.min_assets > 1 ? "s" : ""} with price data and a benchmark with data. Add tickers in the Universe block, or pick a preset.</p>
                  </div>
                </div>
              ) : tab === "backtest" ? (
                <BacktestTab q={run} />
              ) : tab === "sweep" ? (
                <SweepTab key={spec.key} spec={spec} base={base} maxCombos={catalog.data.caps.grid_combinations} onData={setSweepData} />
              ) : tab === "walkforward" ? (
                <WalkForwardTab key={spec.key} spec={spec} base={base} maxCombos={catalog.data.caps.grid_combinations} />
              ) : (
                <CostsTab key={spec.key} base={base} cap={catalog.data.caps.cost_points} />
              )}
            </div>
          </div>
        </div>
      )}
    </Page>
  );
}

function StrategyHero({ spec }: { spec: StrategySpec }) {
  const facts = [
    `Rebalances ${spec.default_rebalance}`,
    spec.default_max_leverage > 1 ? `up to ${spec.default_max_leverage}× gross` : "unlevered",
    spec.min_assets > 1 ? `needs ${spec.min_assets}+ assets` : null,
    spec.needs_market ? "uses the benchmark as the market" : null,
  ].filter(Boolean);
  return (
    <header className="sl-hero">
      <div className="eyebrow">{CATEGORY_LABEL[spec.category] ?? spec.category}</div>
      <h2 className="sl-hero-title display">{spec.name}</h2>
      <p className="sl-hero-text">{spec.explanation}</p>
      <div className="sl-hero-cite">
        <Icon name="book" size={14} />
        <span>{spec.citation}</span>
      </div>
      <div className="sl-hero-facts small">
        {facts.map((f) => (
          <span key={f as string}>{f}</span>
        ))}
        {spec.notes.map((n) => (
          <span key={n} className="subtle">{n}</span>
        ))}
      </div>
    </header>
  );
}

function RunBar({ dirty, runnable, settled, fetching, hasRun, onRun, usable, window, failed }: { dirty: boolean; runnable: boolean; settled: boolean; fetching: boolean; hasRun: boolean; onRun: () => void; usable: number; window?: RunOut["window"]; failed: boolean }) {
  let status: React.ReactNode;
  if (!settled) status = <span className="subtle">Checking which tickers have data…</span>;
  else if (!runnable) status = <span className="warn">Add assets with data (and a benchmark with data) to run.</span>;
  else if (fetching) status = <span className="subtle">Running on {usable} assets…</span>;
  else if (dirty && hasRun) status = <span className="sl-dirty">Inputs changed — run to update the results below.</span>;
  else if (failed) status = <span className="loss">The last run failed — see below.</span>;
  else if (hasRun && window) status = <span className="subtle">Showing {usable} assets, live {fmtDate(window.live_start)} – {fmtDate(window.end)}.</span>;
  else status = <span className="subtle">Ready.</span>;
  return (
    <div className="sl-runbar">
      <button type="button" className="btn btn-primary sl-run" onClick={onRun} disabled={!runnable || fetching || (!dirty && !failed)}>
        <Icon name="research" size={15} /> {fetching ? "Running…" : "Run backtest"}
      </button>
      <span className="small">{status}</span>
    </div>
  );
}
