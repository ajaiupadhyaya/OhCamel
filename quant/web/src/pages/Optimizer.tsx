/**
 * Optimizer (/optimize) — turn a universe into weights, and test honestly whether the
 * method would have worked.
 *
 * Data (all real, from quant/src/ohcamel_quant/api/routers/portfolio.py):
 *   GET  /api/portfolio/methods     method catalog (descriptions, references)
 *   POST /api/portfolio/optimize    weights + ex-ante diagnostics (+ BL posterior, HRP tree)
 *   POST /api/portfolio/frontier    constrained frontier, CML, assets, other methods
 *   POST /api/portfolio/covariance  estimator, clustered correlation, MP spectrum
 *   POST /api/portfolio/compare     walk-forward OOS comparison + Sharpe-difference tests
 * Light endpoints re-query as inputs change (debounced); the walk-forward backtest runs on
 * an explicit button. Deep-link (from Portfolio Lab):
 *   /optimize?tickers=SPY,TLT&weights=0.6,0.4&start=YYYY-MM-DD&end=YYYY-MM-DD&from=portfolio
 * Page-local components live in ./optimizer/ (CSS prefix `op-`).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Page, Skeleton, Tabs, useTabParam, InfoTip } from "../components";
import { Icon } from "../components/Icon";
import { fmtDate } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { usePortfolio, type Portfolio } from "../lib/portfolio";
import { useApiPost, useApiQuery } from "../lib/query";
import { AllocationTab } from "./optimizer/AllocationTab";
import { BlackLittermanTab } from "./optimizer/BlackLittermanTab";
import { CompareTab } from "./optimizer/CompareTab";
import {
  DEFAULTS,
  currentEvalBody,
  frontierBody,
  optimizeBody,
  parseDeepLink,
  type OptConfig,
} from "./optimizer/config";
import { CovarianceTab } from "./optimizer/CovarianceTab";
import { FrontierTab } from "./optimizer/FrontierTab";
import { METHOD_FAMILY } from "./optimizer/info";
import {
  MethodHeader,
  MethodParams,
  MethodPicker,
  methodInfo,
} from "./optimizer/MethodPicker";
import { SetupRail } from "./optimizer/SetupRail";
import { OptProvider, isRfError, type OptCtx } from "./optimizer/shared";
import type {
  FrontierOut,
  MethodsCatalog,
  OptimizeOut,
} from "./optimizer/types";
import "./optimizer/optimizer.css";

type Tab = "allocation" | "frontier" | "views" | "covariance" | "backtest";
const PORTFOLIO_SOURCE = "portfolio";

function portfolioWeights(p: Portfolio): Record<string, number> {
  const w: Record<string, number> = {};
  for (const h of p.holdings) w[h.ticker] = (w[h.ticker] ?? 0) + h.weight;
  return w;
}

function initialConfig(sp: URLSearchParams, p: Portfolio): OptConfig {
  const deep = parseDeepLink(sp);
  const isoParam = (k: string) => {
    const v = sp.get(k);
    return v && /^\d{4}-\d{2}-\d{2}$/.test(v) ? v : undefined;
  };
  const base = {
    ...DEFAULTS,
    start: isoParam("start") ?? p.start,
    end: isoParam("end") ?? p.end,
    benchmark: p.benchmark || "SPY",
  };
  if (deep) {
    const hasW = Object.keys(deep.weights).length > 0;
    return {
      ...base,
      tickers: deep.tickers,
      current: hasW ? deep.weights : {},
      currentSource: hasW ? "Portfolio Lab link" : PORTFOLIO_SOURCE,
    };
  }
  const tickers = [
    ...new Set(p.holdings.filter((h) => h.weight !== 0).map((h) => h.ticker)),
  ];
  return { ...base, tickers, current: {}, currentSource: PORTFOLIO_SOURCE };
}

export default function Optimizer() {
  const [sp, setSp] = useSearchParams();
  const { portfolio, setHoldings } = usePortfolio();
  const [raw, setRaw] = useState<OptConfig>(() => initialConfig(sp, portfolio));
  const [tab, setTab] = useTabParam<Tab>("tab", "allocation");
  const set = useCallback(
    (p: Partial<OptConfig>) => setRaw((c) => ({ ...c, ...p })),
    [],
  );

  // the deep-link has been adopted into state; drop it so a reload doesn't clobber edits
  useEffect(() => {
    if (sp.has("tickers") || sp.has("weights") || sp.has("start") || sp.has("end")) {
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev);
          for (const k of ["tickers", "weights", "start", "end", "from"]) n.delete(k);
          return n;
        },
        { replace: true },
      );
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // "current" follows the live portfolio unless it came from a deep-link
  const pw = useMemo(() => portfolioWeights(portfolio), [portfolio]);
  const cfg = useMemo<OptConfig>(
    () =>
      raw.currentSource === PORTFOLIO_SOURCE
        ? { ...raw, current: pw, currentSource: portfolio.name }
        : raw,
    [raw, pw, portfolio.name],
  );

  const catalog = useApiQuery<MethodsCatalog>("/portfolio/methods", undefined, {
    staleTime: Infinity,
  });
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, {
    staleTime: Infinity,
  });
  const ready = cfg.tickers.length >= 2;

  const optBody = useDebounced(
    useMemo(() => optimizeBody(cfg), [cfg]),
    350,
  );
  const opt = useApiPost<OptimizeOut>("/portfolio/optimize", optBody, {
    enabled: ready,
  });
  const frBody = useDebounced(
    useMemo(() => frontierBody(cfg), [cfg]),
    350,
  );
  const frontier = useApiPost<FrontierOut>("/portfolio/frontier", frBody, {
    enabled: ready && tab === "frontier",
  });
  const curBody = useDebounced(
    useMemo(() => currentEvalBody(cfg), [cfg]),
    350,
  );
  const currentEval = useApiPost<OptimizeOut>("/portfolio/optimize", curBody, {
    enabled: ready && tab === "frontier" && !!curBody,
  });

  const spec = catalog.data?.methods.find((m) => m.name === cfg.method);
  const rfMissing =
    isRfError(opt.error) ||
    isRfError(frontier.error) ||
    (opt.data?.risk_free.source === null && cfg.rfMode === "market");

  const applied = useMemo(() => {
    const w = opt.data?.result.weights;
    if (!w) return false;
    const live = Object.entries(w).filter(([, v]) => Math.abs(v) > 1e-4);
    return (
      live.length === portfolio.holdings.length &&
      live.every(([k, v]) => Math.abs((pw[k] ?? NaN) - v) < 1e-4)
    );
  }, [opt.data, portfolio.holdings.length, pw]);

  const apply = useCallback(
    (d: OptimizeOut) => {
      const hs = Object.entries(d.result.weights)
        .filter(([, v]) => Math.abs(v) > 1e-4)
        .map(([ticker, weight]) => ({ ticker, weight: +weight.toFixed(6) }));
      setHoldings(hs);
      set({ currentSource: PORTFOLIO_SOURCE, current: {} });
    },
    [setHoldings, set],
  );

  const adoptPortfolio = useCallback(() => {
    const tickers = [
      ...new Set(
        portfolio.holdings.filter((h) => h.weight !== 0).map((h) => h.ticker),
      ),
    ];
    set({
      tickers,
      currentSource: PORTFOLIO_SOURCE,
      current: {},
      start: portfolio.start,
      end: portfolio.end,
      benchmark: portfolio.benchmark || "SPY",
    });
  }, [portfolio, set]);

  const ctx = useMemo<OptCtx>(
    () => ({ cfg, set, catalog: catalog.data, spec, rfMissing }),
    [cfg, set, catalog.data, spec, rfMissing],
  );
  const u = opt.data?.universe;

  const tabs = [
    { id: "allocation" as const, label: "Allocation" },
    { id: "frontier" as const, label: "Frontier" },
    {
      id: "views" as const,
      label: "Black–Litterman",
      badge:
        cfg.returns === "black_litterman"
          ? String(cfg.views.length)
          : undefined,
    },
    { id: "covariance" as const, label: "Covariance" },
    { id: "backtest" as const, label: "Backtest vs 1/N" },
  ];

  return (
    <OptProvider value={ctx}>
      <Page
        eyebrow="Portfolio construction"
        title="Optimizer"
        subtitle="Turn a universe of assets into weights with ten allocation methods — then check, out of sample and after costs, whether any of them actually beats splitting the money equally."
        meta={
          <>
            {health.data?.offline && (
              <span
                className="badge unknown"
                title="The backend is running in offline mode: only committed daily data (9 ETFs, 2016–2026, a few FRED series) is available."
              >
                Offline dataset
              </span>
            )}
            {u && (
              <span>
                <span className="num">{u.tickers.length}</span> assets ·{" "}
                <span className="num">{u.observations.toLocaleString()}</span>{" "}
                common trading days · {fmtDate(u.start)} – {fmtDate(u.end)}
              </span>
            )}
            {opt.isFetching && !opt.isLoading && (
              <span className="op-updating">
                <span className="op-dot" /> updating
              </span>
            )}
          </>
        }
      >
        <div className="op-layout">
          <SetupRail
            cfg={cfg}
            set={set}
            catalog={catalog.data}
            portfolioName={portfolio.name}
            onUsePortfolio={adoptPortfolio}
            anchor={u?.end}
          />

          <div className="op-main">
            <section
              className="op-method-block"
              aria-labelledby="op-method-title"
            >
              <header className="op-block-head">
                <div>
                  <h2 id="op-method-title" className="op-block-title display">
                    Method
                    <InfoTip info={methodInfo(spec)} />
                  </h2>
                  <p className="op-block-desc">
                    Ten ways to turn estimates into weights, from naive to
                    hierarchical. Results update as you change anything.
                  </p>
                </div>
                {spec && (
                  <span className="badge accent">
                    {METHOD_FAMILY[spec.name]}
                  </span>
                )}
              </header>
              {catalog.isLoading ? (
                <div className="op-methods">
                  {Array.from({ length: 10 }, (_, i) => (
                    <Skeleton key={i} height={120} />
                  ))}
                </div>
              ) : catalog.data ? (
                <MethodPicker
                  methods={catalog.data.methods}
                  cfg={cfg}
                  set={set}
                  rfMissing={rfMissing}
                />
              ) : (
                <div className="op-warn small">
                  <Icon name="alert" size={14} /> The method catalog could not
                  be loaded ({catalog.error?.detail}).
                </div>
              )}
              {spec && (
                <div className="op-method-foot">
                  <MethodHeader spec={spec} />
                  <MethodParams cfg={cfg} set={set} />
                </div>
              )}
            </section>

            <div className="op-tabs">
              <Tabs<Tab> items={tabs} value={tab} onChange={setTab} />
            </div>

            {!ready ? (
              <div className="op-empty-universe">
                <Icon name="optimize" size={22} />
                <p className="display">
                  Add at least two assets to the universe to begin.
                </p>
                <button
                  type="button"
                  className="btn btn-primary"
                  onClick={adoptPortfolio}
                >
                  <Icon name="portfolio" size={15} /> Use my portfolio
                </button>
              </div>
            ) : (
              <>
                {tab === "allocation" && (
                  <AllocationTab q={opt} onApply={apply} applied={applied} />
                )}
                {tab === "frontier" && (
                  <FrontierTab
                    q={frontier}
                    opt={opt}
                    current={curBody ? currentEval : null}
                  />
                )}
                {tab === "views" && <BlackLittermanTab q={opt} />}
                {tab === "covariance" && (
                  <CovarianceTab enabled={tab === "covariance"} />
                )}
                {/* kept mounted so a finished backtest survives tab switches */}
                <div hidden={tab !== "backtest"}>
                  <CompareTab enabled={tab === "backtest"} />
                </div>
              </>
            )}
          </div>
        </div>
      </Page>
    </OptProvider>
  );
}
