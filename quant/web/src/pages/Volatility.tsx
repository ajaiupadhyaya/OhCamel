/**
 * Volatility (/options?ticker=SPY) — what options price and what the underlying delivers.
 *
 * Tabs (state in the URL: ?ticker= &tab= &expiry= &rw= &ry= &re= &spread=):
 *  - Realized   GET  /api/options/realized/{t}           (offline OK: committed OHLC + FRED VIX)
 *  - Surface    GET  /api/options/surface/{t}, /chain/{t} (live Cboe chain; 503 offline)
 *  - Density    GET  /api/options/density/{t}?expiry=     (live)
 *  - Chain      GET  /api/options/chain/{t}?expiry=  +  POST /api/options/strategy on "Analyze"
 *  - Calculator POST /api/options/price                   (offline OK when r is supplied)
 * The expiry list (GET /api/options/expiries/{t}) gates every chain-dependent query; when it
 * is unavailable those tabs explain what they compute instead of showing empty charts.
 * Page-local components live in ./volatility/ (classes prefixed `vx-`).
 */
import { useEffect, useMemo, useState } from "react";
import { Page, Panel, StatGrid, StatTile, Tabs, TickerInput, useTabParam, type TabItem } from "../components";
import { isDataUnavailable } from "../lib/api";
import type { Info } from "../lib/glossary";
import { fmtDate, fmtNum, fmtPct } from "../lib/format";
import { usePortfolio } from "../lib/portfolio";
import { useApiPost, useApiQuery } from "../lib/query";
import { CalculatorTab, seedFrom } from "./volatility/CalculatorTab";
import { ChainTab } from "./volatility/ChainTab";
import { DensityTab } from "./volatility/DensityTab";
import { ImpliedTab } from "./volatility/ImpliedTab";
import { ESTIMATOR_LABEL, INFO } from "./volatility/info";
import { RealizedTab, WINDOWS, YEARS } from "./volatility/RealizedTab";
import { buildPreset } from "./volatility/Strategy";
import { ExpirySelect, LiveOnly, defaultExpiry, fmtDays, useChain, useCommitted, useDensity, useExpiries, useRealized, useSurface } from "./volatility/shared";
import { ESTIMATORS, type Estimator, type LegSpec, type Realized, type StrategyOut, type Surface } from "./volatility/types";
import "./volatility/volatility.css";

type Tab = "realized" | "surface" | "density" | "chain" | "calculator";
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
  const liveUnavailable = isDataUnavailable(liveError);
  const tabs: TabItem<Tab>[] = [
    { id: "realized", label: "Realized" },
    { id: "surface", label: "Smile & surface", badge: liveUnavailable ? "offline" : undefined },
    { id: "density", label: "Density", badge: liveUnavailable ? "offline" : undefined },
    { id: "chain", label: "Chain & strategies", badge: liveUnavailable ? "offline" : undefined },
    { id: "calculator", label: "Calculator" },
  ];
  const sel = expiries.data?.expiries.find((e) => e.slice === expiry);

  return (
    <Page
      eyebrow={realized.data ? `Options & volatility · data to ${fmtDate(realized.data.as_of)}` : "Options & volatility"}
      title={
        <span className="vx-title">
          Volatility <span className="vx-title-ticker num">{ticker}</span>
        </span>
      }
      docTitle={`${ticker} volatility`}
      subtitle="What the options market is pricing — the smile, the surface and the implied distribution of outcomes — set against how much the price actually moves."
      meta={
        health.data?.offline ? (
          <span className="badge unknown" title="The backend runs offline: committed daily bars and FRED fixtures only. Option chains are fetched live from Cboe.">
            Offline dataset — realized vol and the calculator work; live chains are paused
          </span>
        ) : undefined
      }
      actions={
        <div className="vx-actions">
          <TickerInput onSelect={(t) => setTicker(t)} placeholder="Underlying — ticker or name…" className="vx-ticker-input" />
          {holdings.length > 0 && (
            <div className="vx-holdings" aria-label={`Tickers in ${portfolio.name}`}>
              <span className="subtle small" title={portfolio.name}>Portfolio</span>
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
              <span className="vx-slice-meta subtle small">
                <span>F <span className="num">{fmtNum(sel.forward, 2)}</span></span>
                <span>r <span className="num">{fmtPct(sel.rate, 2)}</span></span>
                <span>q <span className="num">{fmtPct(sel.div_yield, 2)}</span></span>
                <span className="num">{fmtDays(sel.dte)}</span>
              </span>
            )}
          </div>
        )}
      </div>

      {tab === "realized" && <RealizedTab q={realized} window={String(window)} setWindow={setRw} estimator={estimator} setEstimator={setRe} years={String(years)} setYears={setRy} />}

      {LIVE_TABS.includes(tab) && !live && (
        <LiveOnlyTab tab={tab} error={liveError} loading={expiries.isLoading} onRetry={() => void expiries.refetch()} goTo={setTab} ticker={ticker} />
      )}

      {tab === "surface" && live && <ImpliedTab surface={surface} chain={chain} expiry={expiry} />}
      {tab === "density" && live && <DensityTab q={density} surface={surface} />}
      {tab === "chain" && live && <ChainTab chain={chain} spread={spread} setSpread={setSpread} legs={legs} setLegs={setLegs} strategy={strategy} run={strat.run} dirty={strat.dirty} />}

      {tab === "calculator" && <CalculatorTab ticker={ticker} seed={seedFrom(realized.data)} seedLoading={realized.isLoading} />}
    </Page>
  );
}

// ------------------------------------------------------------------ snapshot strip
function Snapshot({ realized, surface, sel }: { realized: ReturnType<typeof useRealized>; surface: Surface | undefined; sel: { dte: number; move: number | null } | null }) {
  const r: Realized | undefined = realized.data;
  const yz21 = r?.current["21"]?.yang_zhang;
  const cone21 = r?.cone.find((c) => c.horizon === 21);
  const implied = surface?.atm_30d?.iv ?? r?.vrp?.implied ?? null;
  const impliedSrc = surface?.atm_30d ? "30d ATM, live SVI surface" : r?.vrp ? `${r.vrp.implied_source.split(" (")[0]}, as of ${fmtDate(r.vrp.as_of ?? r.as_of)}` : "needs a live chain or a Cboe index";
  const vrp = implied != null && r ? implied - (r.current["21"]?.close_to_close ?? NaN) : null;
  return (
    <Panel<Realized> query={realized} skeletonHeight={96} notes={[]} className="vx-snapshot">
      {() => (
        <StatGrid min={150}>
          <StatTile size="lg" label="Realized · 21d" value={yz21} format={(v) => fmtPct(v, 1)} info={INFO.yang_zhang} caption="Yang–Zhang, annualized" />
          <StatTile size="lg" label="vs its history" value={cone21 ? cone21.current_pctile : null} format={(v) => `${fmtNum(v * 100, 0)}th pct`} info={INFO.cone_pctile} caption={cone21 ? `${ESTIMATOR_LABEL[r!.estimator]} 1M cone · median ${fmtPct(cone21.p50, 1)}` : undefined} />
          <StatTile size="lg" label="Implied · 30d" value={implied} format={(v) => fmtPct(v, 1)} info={surface?.atm_30d ? INFO.atm_30d : INFO.model_free} caption={impliedSrc} />
          <StatTile size="lg" label="Risk premium" value={vrp != null && Number.isFinite(vrp) ? vrp : null} format={(v) => `${fmtNum(v * 100, 1, { signed: true })} pts`} tone="auto" info={INFO.vrp} caption="implied − 21d close-to-close" />
          {surface?.vix_style_30d ? (
            <StatTile size="lg" label="Model-free · 30d" value={surface.vix_style_30d.index / 100} format={(v) => fmtPct(v, 1)} info={INFO.model_free} caption="VIX methodology on this chain" />
          ) : (
            <StatTile size="lg" label="Implied move" value={sel?.move ?? null} format={(v) => `±${fmtPct(v, 1)}`} info={INFO.implied_move} caption={sel ? `to the selected expiry (${Math.round(sel.dte)}d)` : "needs a live chain"} />
          )}
        </StatGrid>
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ offline treatment
const LIVE_COPY: Record<"surface" | "density" | "chain", { title: string; items: { title: string; text: string; info?: Info }[] }> = {
  surface: {
    title: "Smile & surface",
    items: [
      { title: "Smile per expiry", text: "Every out-of-the-money quote's implied vol with its bid–ask whisker, the SVI curve fitted through them, and the vendor's IV for comparison.", info: INFO.svi },
      { title: "Term structure", text: "ATM-forward vol and model-free (VIX-style) vol for every listed expiry, with 30-day constant-maturity values.", info: INFO.model_free },
      { title: "Skew and convexity", text: "25Δ and 10Δ risk reversals and butterflies across expiries.", info: INFO.rr },
      { title: "3-D surface", text: "Implied vol over log-moneyness and maturity, interpolated in total variance.", info: INFO.atm_iv },
      { title: "Arbitrage diagnostics", text: "Durrleman's butterfly condition per expiry and the calendar condition between expiries, as pass / fail.", info: INFO.butterfly_g },
      { title: "Implied carry", text: "Forward, discount rate and dividend yield of each expiry, read from put-call parity.", info: INFO.parity },
    ],
  },
  density: {
    title: "Risk-neutral density",
    items: [
      { title: "Market-implied distribution", text: "The Breeden–Litzenberger density of the price at expiry against a lognormal at the ATM vol.", info: INFO.rnd },
      { title: "Implied probabilities", text: "P(fall more than 5 / 10 / 20 %) and P(rise more than …) under the pricing measure.", info: INFO.q_prob },
      { title: "Quantiles, moments and the implied move", text: "Price levels by probability, skewness and kurtosis versus lognormal, and the straddle-implied move.", info: INFO.skew },
    ],
  },
  chain: {
    title: "Chain & strategies",
    items: [
      { title: "Option chain", text: "Calls | strike | puts with our IV next to the vendor's, greeks, open interest and volume; click a price to trade it.", info: INFO.iv },
      { title: "Strategy builder", text: "Straddles, strangles, verticals, iron condors and collars, or any 12-leg combination: payoff today and at expiry, breakevens, max P/L, greeks.", info: INFO.pop },
    ],
  },
};

function LiveOnlyTab({ tab, error, loading, onRetry, goTo, ticker }: { tab: Tab; error: unknown; loading: boolean; onRetry: () => void; goTo: (t: Tab) => void; ticker: string }) {
  if (loading) return <Panel title="Loading the option chain…" loading skeletonHeight={320} />;
  const copy = LIVE_COPY[tab as "surface" | "density" | "chain"];
  return (
    <LiveOnly
      title={`${copy.title} · ${ticker}`}
      error={error}
      onRetry={onRetry}
      items={copy.items}
      alternatives={
        <>
          Available now:{" "}
          <button type="button" className="vx-link" onClick={() => goTo("realized")}>realized volatility & VRP</button> ·{" "}
          <button type="button" className="vx-link" onClick={() => goTo("calculator")}>BSM calculator</button>
        </>
      }
    />
  );
}
