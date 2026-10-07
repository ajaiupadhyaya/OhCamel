/**
 * Volatility (/options?ticker=SPY): what options price against what the underlying delivers,
 * plus the two Lane M products on it.
 *
 * Views (state in the URL: ?ticker= &tab= &expiry= &rw= &ry= &re= &spread=):
 *  - REALIZED  GET  /api/options/realized/{t}           (offline OK: committed OHLC + FRED VIX)
 *  - SURFACE   GET  /api/options/surface/{t}, /chain/{t} (live Cboe; 503 offline). The 3-D
 *              surface is the app's one Plotly view (./volatility/SurfaceView, lazy).
 *  - DENSITY   GET  /api/options/density/{t}?expiry=     (live)
 *  - CHAIN     GET  /api/options/chain/{t}?expiry=  +  POST /api/options/strategy on ANALYZE
 *  - CALC      POST /api/options/price                   (offline OK when r is supplied)
 *  - FORECASTS P2   GET /api/artifacts/vol.forecast_league/latest (+ tables)
 *  - HISTORY   P7   GET /api/artifacts/vol.surface_history/latest (+ tables)
 * The expiry list (GET /api/options/expiries/{t}) gates every chain view; without it those
 * views say INSUFFICIENT DATA and list what they compute. Page-local code in ./volatility/.
 */
import { useEffect, useMemo, useState } from "react";
import { Page, Panel, StatGrid, StatTile, Tabs, TickerInput, useTabParam, type TabItem } from "../components";
import { isDataUnavailable } from "../lib/api";
import { fmtDate, fmtNum, fmtPct } from "../lib/format";
import { usePortfolio } from "../lib/portfolio";
import { useApiPost, useApiQuery } from "../lib/query";
import { CalculatorTab, seedFrom } from "./volatility/CalculatorTab";
import { ChainTab } from "./volatility/ChainTab";
import { DensityTab } from "./volatility/DensityTab";
import { ForecastsView } from "./volatility/Forecasts";
import { HistoryView } from "./volatility/History";
import { ImpliedTab } from "./volatility/ImpliedTab";
import { ESTIMATOR_SHORT, INFO } from "./volatility/info";
import { RealizedTab, WINDOWS, YEARS } from "./volatility/RealizedTab";
import { buildPreset } from "./volatility/Strategy";
import { ExpirySelect, LiveOnly, Readline, defaultExpiry, fmtDays, fmtVolPts, useChain, useCommitted, useDensity, useExpiries, useRealized, useSurface } from "./volatility/shared";
import { ESTIMATORS, type Estimator, type LegSpec, type Realized, type StrategyOut, type Surface } from "./volatility/types";
import "./volatility/volatility.css";

type Tab = "realized" | "surface" | "density" | "chain" | "calculator" | "forecasts" | "history";
type LiveTab = "surface" | "density" | "chain";
const LIVE_TABS: Tab[] = ["surface", "density", "chain"];

export default function Volatility() {
  const [rawTicker, setTicker] = useTabParam<string>("ticker", "SPY");
  const ticker = rawTicker.toUpperCase();
  const [tab, setTab] = useTabParam<Tab>("tab", "realized");
  const [expiryParam, setExpiry] = useTabParam<string>("expiry", "");
  const [rw, setRw] = useTabParam<string>("rw", "21");
  const [ry, setRy] = useTabParam<string>("ry", "10");
  const [re, setRe] = useTabParam<Estimator>("re", "yang_zhang");
  const [spread, setSpread] = useTabParam<string>("spread", "0.5");
  const { portfolio } = usePortfolio();
  const health = useApiQuery<{ offline?: boolean }>("/health", undefined, { staleTime: Infinity });

  const window = (WINDOWS as readonly string[]).includes(rw) ? +rw : 21;
  const years = (YEARS as readonly string[]).includes(ry) ? +ry : 10;
  const estimator: Estimator = (ESTIMATORS as readonly string[]).includes(re) ? re : "yang_zhang";

  // ---- data
  const realized = useRealized(ticker, window, estimator, years);
  const expiries = useExpiries(ticker);
  const live = !!expiries.data;
  const liveError = expiries.error;
  const expiry = expiries.data?.expiries.some((e) => e.slice === expiryParam) ? expiryParam : defaultExpiry(expiries.data?.expiries);
  const surface = useSurface(ticker, live);
  const chain = useChain(ticker, expiry, +spread || 0.5, live && (tab === "surface" || tab === "chain"));
  const density = useDensity(ticker, expiry, live && tab === "density");

  // ---- strategy ticket (lives here so it survives tab switches)
  const [legs, setLegs] = useState<LegSpec[]>([]);
  const strat = useCommitted<LegSpec[] | null>(legs, null);
  useEffect(() => {
    setLegs([]);
    strat.commit(null);
  }, [ticker]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // first chain for this ticker: open with an ATM straddle so the builder shows a result
    if (chain.data && chain.data.underlying === ticker && strat.committed === null && legs.length === 0) {
      const s = buildPreset("straddle", chain.data);
      if (s.length) {
        setLegs(s);
        strat.commit(s);
      }
    }
  }, [chain.data, ticker]); // eslint-disable-line react-hooks/exhaustive-deps
  const committedLegs = strat.committed?.filter((l) => l.qty !== 0) ?? [];
  const strategy = useApiPost<StrategyOut>("/options/strategy", { ticker, legs: committedLegs, n_dates: 4 }, { enabled: live && committedLegs.some((l) => l.kind === "option") });

  const holdings = useMemo(() => [...new Set(portfolio.holdings.map((h) => h.ticker.toUpperCase()))].slice(0, 10), [portfolio.holdings]);
  const offlineBadge = isDataUnavailable(liveError) ? "OFFLINE" : undefined;
  const tabs: TabItem<Tab>[] = [
    { id: "realized", label: "Realized" },
    { id: "surface", label: "Surface", badge: offlineBadge },
    { id: "density", label: "Density", badge: offlineBadge },
    { id: "chain", label: "Chain", badge: offlineBadge },
    { id: "calculator", label: "Calc" },
    { id: "forecasts", label: "Forecasts · P2" },
    { id: "history", label: "History · P7" },
  ];
  const sel = expiries.data?.expiries.find((e) => e.slice === expiry);

  return (
    <Page
      title="Volatility"
      docTitle={`${ticker} volatility`}
      meta={
        <>
          <span>{ticker}</span>
          {realized.data && <span>DATA {fmtDate(realized.data.as_of).toUpperCase()}</span>}
          {health.data?.offline && <span>OFFLINE DATASET · LIVE CHAINS PAUSED</span>}
        </>
      }
      actions={
        <div className="vx-actions">
          <TickerInput onSelect={(t) => setTicker(t)} placeholder="UNDERLYING" className="vx-ticker-input" />
          {holdings.length > 0 && (
            <div className="vx-holdings" aria-label={`Tickers in ${portfolio.name}`}>
              <span className="vx-holdings-k">BOOK</span>
              {holdings.map((t) => (
                <button key={t} type="button" className={`vx-chip num ${t === ticker ? "active" : ""}`} onClick={() => setTicker(t)} aria-pressed={t === ticker}>
                  {t}
                </button>
              ))}
            </div>
          )}
        </div>
      }
    >
      <Snapshot realized={realized} surface={surface.data} sel={sel ? { dte: sel.dte, move: surface.data?.term_structure.find((r) => r.slice === sel.slice)?.implied_move ?? null } : null} />

      <div className="vx-tabbar">
        <Tabs items={tabs} value={tab} onChange={setTab} />
        {LIVE_TABS.includes(tab) && expiries.data && (
          <div className="vx-tabbar-tools">
            <ExpirySelect expiries={expiries.data.expiries} value={expiry} onChange={setExpiry} />
            {sel && (
              <Readline
                items={[
                  { k: "F", v: fmtNum(sel.forward, 2) },
                  { k: "r", v: fmtPct(sel.rate, 2) },
                  { k: "q", v: fmtPct(sel.div_yield, 2) },
                  { k: "T", v: fmtDays(sel.dte) },
                ]}
              />
            )}
          </div>
        )}
      </div>

      {tab === "realized" && <RealizedTab q={realized} window={String(window)} setWindow={setRw} estimator={estimator} setEstimator={setRe} years={String(years)} setYears={setRy} />}

      {LIVE_TABS.includes(tab) && !live && <LiveOnlyTab tab={tab as LiveTab} error={liveError} loading={expiries.isLoading} onRetry={() => void expiries.refetch()} goTo={setTab} ticker={ticker} />}

      {tab === "surface" && live && <ImpliedTab surface={surface} chain={chain} expiry={expiry} />}
      {tab === "density" && live && <DensityTab q={density} surface={surface} />}
      {tab === "chain" && live && <ChainTab chain={chain} spread={spread} setSpread={setSpread} legs={legs} setLegs={setLegs} strategy={strategy} run={strat.run} dirty={strat.dirty} />}

      {tab === "calculator" && <CalculatorTab ticker={ticker} seed={seedFrom(realized.data)} seedLoading={realized.isLoading} />}
      {tab === "forecasts" && <ForecastsView ticker={ticker} />}
      {tab === "history" && <HistoryView ticker={ticker} />}
    </Page>
  );
}

// ------------------------------------------------------------------ snapshot strip
function Snapshot({ realized, surface, sel }: { realized: ReturnType<typeof useRealized>; surface: Surface | undefined; sel: { dte: number; move: number | null } | null }) {
  const r: Realized | undefined = realized.data;
  const yz21 = r?.current["21"]?.yang_zhang;
  const cone21 = r?.cone.find((c) => c.horizon === 21);
  const implied = surface?.atm_30d?.iv ?? r?.vrp?.implied ?? null;
  const impliedSrc = surface?.atm_30d ? "ATM · LIVE SVI" : r?.vrp ? `${r.vrp.implied_source.split(" (")[0].toUpperCase()} · ${fmtDate(r.vrp.as_of ?? r.as_of).toUpperCase()}` : "NO CHAIN · NO CBOE INDEX";
  const vrp = implied != null && r ? implied - (r.current["21"]?.close_to_close ?? NaN) : null;
  return (
    <Panel<Realized> query={realized} skeletonHeight={96} notes={[]} className="vx-snapshot" asOf={r?.as_of}>
      {() => (
        <StatGrid min={150}>
          <StatTile size="lg" label="Realized · 21D" value={yz21} format={(v) => fmtPct(v, 1)} info={INFO.yang_zhang} caption={`${ESTIMATOR_SHORT.yang_zhang} · ANN`} />
          <StatTile size="lg" label="Pctile · 1M cone" value={cone21 ? cone21.current_pctile : null} format={(v) => fmtNum(v * 100, 0)} info={INFO.cone_pctile} caption={cone21 ? `${ESTIMATOR_SHORT[r!.estimator]} · MEDIAN ${fmtPct(cone21.p50, 1)}` : undefined} />
          <StatTile size="lg" label="Implied · 30D" value={implied} format={(v) => fmtPct(v, 1)} info={surface?.atm_30d ? INFO.atm_30d : INFO.model_free} caption={impliedSrc} />
          <StatTile size="lg" label="VRP · pts" value={vrp != null && Number.isFinite(vrp) ? vrp : null} format={(v) => fmtVolPts(v)} tone="auto" info={INFO.vrp} caption="IMPLIED − CC RV 21D" />
          {surface?.vix_style_30d ? (
            <StatTile size="lg" label="Model-free · 30D" value={surface.vix_style_30d.index / 100} format={(v) => fmtPct(v, 1)} info={INFO.model_free} caption="CBOE METHOD · THIS CHAIN" />
          ) : (
            <StatTile size="lg" label="Implied move" value={sel?.move ?? null} format={(v) => `±${fmtPct(v, 1)}`} info={INFO.implied_move} caption={sel ? `TO EXPIRY · ${Math.round(sel.dte)}D` : "NEEDS A LIVE CHAIN"} />
          )}
        </StatGrid>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ offline treatment
const LIVE_COPY: Record<LiveTab, { title: string; items: { k: string; note: string }[] }> = {
  surface: {
    title: "Surface",
    items: [
      { k: "SMILE · SVI PER EXPIRY · BID–ASK", note: "svi" },
      { k: "TERM · ATM · MODEL-FREE · 30D CONSTANT MATURITY", note: "smile-metrics" },
      { k: "SKEW · RR · BF · 25Δ · 10Δ", note: "smile-metrics" },
      { k: "SURFACE · 3-D · TOTAL VARIANCE IN T", note: "svi" },
      { k: "ARBITRAGE · BUTTERFLY · CALENDAR", note: "svi" },
      { k: "CARRY · PARITY F · r · q", note: "implied-vol" },
    ],
  },
  density: {
    title: "Density",
    items: [
      { k: "Q DENSITY · BREEDEN–LITZENBERGER · LOGNORMAL", note: "density" },
      { k: "P(MOVE) · Q", note: "density" },
      { k: "QUANTILES · MOMENTS · IMPLIED MOVE", note: "density" },
    ],
  },
  chain: {
    title: "Chain",
    items: [
      { k: "CHAIN · IV · VENDOR IV · GREEKS · OI · VOLUME", note: "implied-vol" },
      { k: "TICKET · ≤ 12 LEGS · P&L · BREAKEVENS · GREEKS", note: "strategy-payoff" },
    ],
  },
};

function LiveOnlyTab({ tab, error, loading, onRetry, goTo, ticker }: { tab: LiveTab; error: unknown; loading: boolean; onRetry: () => void; goTo: (t: Tab) => void; ticker: string }) {
  if (loading) return <Panel title={LIVE_COPY[tab].title} loading skeletonHeight={320} />;
  const copy = LIVE_COPY[tab];
  return (
    <LiveOnly
      title={`${copy.title} · ${ticker}`}
      error={error}
      source={`/api/options/expiries/${ticker} · CBOE`}
      items={copy.items}
      actions={
        <>
          {!isDataUnavailable(error) && (
            <button type="button" className="btn btn-sm" onClick={onRetry}>
              RETRY
            </button>
          )}
          <button type="button" className="oc-go vx-link" onClick={() => goTo("realized")}>
            REALIZED →
          </button>
          <button type="button" className="oc-go vx-link" onClick={() => goTo("calculator")}>
            CALC →
          </button>
        </>
      }
    />
  );
}
