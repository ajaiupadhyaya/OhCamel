/**
 * BSM calculator (POST /api/options/price): price and every greek, implied vol from a price,
 * the put-call parity check and value/greek profiles across spot. Inputs are seeded from
 * real data: spot = the underlying's last close, σ = its 30-day implied (VIX-style index)
 * or 21-day realized vol, r = FRED 3-month bill (server-side) — falling back to the 2-year
 * Treasury when the bill series is unavailable (offline), or a rate the user types.
 */
import { useEffect, useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Callout, Chart, Field, NumberField, Panel, SegmentedControl, withAlpha } from "../../components";
import { isDataUnavailable } from "../../lib/api";
import { fmtDate, fmtNum, fmtPct, fmtPctPoints } from "../../lib/format";
import { useDebounced } from "../../lib/hooks";
import { useApiPost, useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { GreekGrid, KV } from "./shared";
import type { FredSeries, Greeks, PriceOut, Realized } from "./types";

type RMode = "bill" | "t2y" | "manual";
type Mode = "price" | "iv";

export interface CalcSeed {
  S: number;
  sigma: number;
  sigmaSource: string;
  asOf: string;
}

export function seedFrom(r: Realized | undefined): CalcSeed | null {
  if (!r) return null;
  const S = r.close.values[r.close.values.length - 1];
  if (S == null) return null;
  const iv = r.vrp?.implied;
  const rv = r.current["21"]?.close_to_close;
  if (iv != null) return { S, sigma: iv, sigmaSource: r.vrp!.implied_source.split(" (")[0] + " 30-day implied", asOf: r.as_of };
  if (rv != null) return { S, sigma: rv, sigmaSource: "21-day close-to-close realized vol", asOf: r.as_of };
  return null;
}

export function CalculatorTab({ ticker, seed, seedLoading }: { ticker: string; seed: CalcSeed | null; seedLoading: boolean }) {
  if (!seed) {
    return <Panel title="Option calculator" loading={seedLoading} error={seedLoading ? undefined : new Error(`No price history for ${ticker} to seed the calculator.`)} skeletonHeight={420} />;
  }
  return <Calculator key={`${ticker}-${seed.S}`} ticker={ticker} seed={seed} />;
}

function Calculator({ ticker, seed }: { ticker: string; seed: CalcSeed }) {
  const roundK = (s: number) => (s >= 200 ? Math.round(s / 5) * 5 : s >= 20 ? Math.round(s) : +s.toFixed(1));
  const [type, setType] = useState<"C" | "P">("C");
  const [mode, setMode] = useState<Mode>("price");
  const [S, setS] = useState(+seed.S.toFixed(2));
  const [K, setK] = useState(roundK(seed.S));
  const [days, setDays] = useState(30);
  const [sigma, setSigma] = useState(+seed.sigma.toFixed(4));
  const [px, setPx] = useState<number | null>(null);
  const [q, setQ] = useState(0);
  const [rMode, setRMode] = useState<RMode>("bill");
  const [rManual, setRManual] = useState(0.04);
  const [touched, setTouched] = useState(false);
  const [fellBack, setFellBack] = useState(false);

  const t2y = useApiQuery<FredSeries>("/macro/series", { ids: "DGS2" }, { enabled: rMode === "t2y", staleTime: Infinity });
  const y2 = t2y.data?.summary?.DGS2;
  // DGS2 is a semi-annual bond-equivalent par yield (percent) -> continuous: 2 ln(1 + y/2)
  const r2y = y2?.latest != null ? 2 * Math.log1p(y2.latest / 100 / 2) : null;

  const r = rMode === "bill" ? undefined : rMode === "t2y" ? r2y : rManual;
  const body = useMemo(() => {
    if (rMode === "t2y" && r == null) return null;
    const base = { S, K, T: days / 365, q, type, ...(r != null ? { r: +r.toFixed(6) } : {}) };
    return mode === "price" ? { ...base, sigma } : px != null ? { ...base, price: px } : null;
  }, [S, K, days, q, type, r, rMode, mode, sigma, px]);
  const dBody = useDebounced(body, 250);
  const res = useApiPost<PriceOut>("/options/price", dBody, { enabled: dBody != null });

  // the 3M bill lives on FRED, which is online-only: fall back to the 2Y Treasury once, visibly
  useEffect(() => {
    if (rMode === "bill" && isDataUnavailable(res.error) && !touched) {
      setTouched(true);
      setFellBack(true);
      setRMode("t2y");
    }
  }, [res.error, rMode, touched]);

  // entering IV mode: seed the price with the current model value
  const switchMode = (m: Mode) => {
    if (m === "iv" && px == null && res.data) setPx(+res.data.result.price.toFixed(2));
    setMode(m);
  };

  const d = res.data;
  const rNote =
    rMode === "bill"
      ? "3-month Treasury bill (FRED DGS3MO), converted server-side from its bond-equivalent yield to a continuous rate."
      : rMode === "t2y"
        ? y2?.latest != null
          ? `2-year Treasury (FRED DGS2) ${fmtPctPoints(y2.latest, 2)} on ${fmtDate(y2.date)}, converted in your browser to continuous: r = 2 ln(1 + y/2) = ${fmtPct(r2y, 3)}.`
          : "Loading the 2-year Treasury yield…"
        : "Your own continuously-compounded rate.";

  return (
    <div className="vx-calc">
      <Panel title="Inputs" subtitle={`Seeded from ${ticker}'s close of ${fmtDate(seed.asOf)} and its ${seed.sigmaSource}. Edit anything — results update as you type.`} info={INFO.bsm} className="vx-calc-inputs">
        <div className="stack">
          <div className="row-wrap">
            <SegmentedControl ariaLabel="Option type" options={[{ value: "C", label: "Call" }, { value: "P", label: "Put" }]} value={type} onChange={setType} />
            <SegmentedControl ariaLabel="Solve for" options={[{ value: "price", label: "Price from vol" }, { value: "iv", label: "Vol from price" }]} value={mode} onChange={switchMode} />
          </div>
          <div className="vx-calc-fields">
            <NumberField label="Spot S" value={S} onChange={setS} min={0.01} step={1} digits={2} info={{ text: `Underlying price. Seeded with ${ticker}'s last committed close.` }} />
            <NumberField label="Strike K" value={K} onChange={setK} min={0.01} step={K >= 200 ? 5 : 1} digits={2} />
            <NumberField label="Days to expiry" value={days} onChange={(v) => setDays(Math.max(1, Math.round(v)))} min={1} max={3650} step={1} digits={0} unit="d" hint={`T = ${fmtNum(days / 365, 4)} y (ACT/365)`} />
            {mode === "price" ? (
              <NumberField label="Volatility σ" value={sigma} onChange={setSigma} percent min={0.005} max={5} step={0.005} digits={2} info={{ text: `Annualized volatility. Seeded with ${seed.sigmaSource} (${fmtPct(seed.sigma, 1)}).` }} />
            ) : (
              <NumberField label="Option price" value={px} onChange={setPx} min={0.0001} step={0.05} digits={4} info={{ text: "Market price of the option; the calculator solves for the volatility that reproduces it.", reference: "Corrado & Miller (1996) start; safeguarded Newton + Brent (1973)" }} />
            )}
            <NumberField label="Dividend yield q" value={q} onChange={setQ} percent min={-0.5} max={1} step={0.0025} digits={2} info={{ text: "Continuous dividend (or borrow) yield. The live chain's parity-implied q per expiry is on the Surface tab." }} />
          </div>
          <Field label="Risk-free rate r" info={{ text: "Continuously compounded rate used for discounting and the forward.", formula: "F = S e^{(r - q)T},\\quad D = e^{-rT}" }}>
            <div className="row-wrap">
              <SegmentedControl
                size="sm"
                ariaLabel="Rate source"
                options={[
                  { value: "bill", label: "3M bill", title: "FRED DGS3MO (server)" },
                  { value: "t2y", label: "2Y Treasury", title: "FRED DGS2" },
                  { value: "manual", label: "Manual" },
                ]}
                value={rMode}
                onChange={(v) => {
                  setTouched(true);
                  setFellBack(false);
                  setRMode(v);
                }}
              />
              {rMode === "manual" && <NumberField value={rManual} onChange={setRManual} percent min={-0.2} max={1} step={0.0025} digits={3} width={120} />}
            </div>
          </Field>
          <p className="subtle small vx-rnote">{rNote}</p>
          {fellBack && rMode === "t2y" && (
            <Callout tone="warn" title="3-month bill unavailable">FRED DGS3MO can't be reached from this server, so the 2-year Treasury stands in. For a 30-day option the tenor mismatch moves the price by cents; switch to Manual to use your own rate.</Callout>
          )}
        </div>
      </Panel>

      <Panel<PriceOut>
        title={type === "C" ? "Call" : "Put"}
        subtitle={d ? `${fmtNum(d.inputs.S, 2)} spot · ${fmtNum(d.inputs.K, 2)} strike · ${fmtNum(d.inputs.T * 365, 0)} days · σ ${fmtPct(d.inputs.sigma, 2)} · r ${fmtPct(d.inputs.r, 3)} · q ${fmtPct(d.inputs.q, 2)}` : undefined}
        info={INFO.bsm}
        query={res}
        skeletonHeight={420}
        className="vx-calc-out"
      >
        {(o) => <Output o={o} mode={mode} />}
      </Panel>
    </div>
  );
}

function Output({ o, mode }: { o: PriceOut; mode: Mode }) {
  const g = o.result;
  return (
    <div className="stack">
      <div className="vx-calc-hero">
        <div>
          <div className="eyebrow">{mode === "iv" ? "Implied volatility" : "Fair value"}</div>
          <div className="vx-calc-price num">{mode === "iv" ? fmtPct(o.implied_vol, 2) : fmtNum(g.price, 4)}</div>
          <div className="subtle small">
            {mode === "iv" ? `reprices ${fmtNum(o.inputs.price, 4)}` : `${fmtNum(g.price * 100, 2)} per 100-share contract`} · forward {fmtNum(o.forward, 2)} · discount {fmtNum(o.discount, 5)}
          </div>
        </div>
        <KV
          rows={[
            { label: "Call", value: fmtNum(o.call.price, 4) },
            { label: "Put", value: fmtNum(o.put.price, 4) },
            { label: "Parity gap", value: o.parity_check.gap.toExponential(1), info: { title: "Put-call parity", text: "Call minus put must equal the discounted forward minus the discounted strike; the gap should be zero to machine precision.", formula: "C - P = S e^{-qT} - K e^{-rT}", reference: "Stoll (1969), J. Finance 24(5)" } },
          ]}
        />
      </div>
      <GreekGrid
        cells={[
          { label: "Delta", value: fmtNum(g.delta, 4), info: INFO.delta },
          { label: "Gamma", value: fmtNum(g.gamma, 5), info: INFO.gamma },
          { label: "Vega", value: fmtNum(g.vega / 100, 4), info: INFO.vega, caption: "per vol pt" },
          { label: "Theta", value: fmtNum(g.theta_day, 4), info: INFO.theta, caption: `per day · ${fmtNum(g.theta, 2)}/yr` },
          { label: "Rho", value: fmtNum(g.rho / 100, 4), info: INFO.rho, caption: "per 1% rate" },
          { label: "Vanna", value: fmtNum(g.vanna, 4), info: INFO.vanna },
          { label: "Volga", value: fmtNum(g.volga, 3), info: INFO.volga },
          { label: "Charm", value: fmtNum(g.charm_day, 5), info: INFO.charm, caption: "per day" },
          { label: "d₁", value: fmtNum(g.d1, 4), info: { title: "d₁ and d₂", text: "N(d₂) is the risk-neutral probability the option finishes in the money; e^{-qT}N(d₁) is the call delta.", formula: "d_{1,2} = \\frac{\\ln(S/K) + (r - q \\pm \\tfrac12\\sigma^2)T}{\\sigma\\sqrt T}" } },
          { label: "d₂", value: fmtNum(g.d2, 4), caption: `N(d₂) ≈ P(ITM)` },
        ]}
      />
      <Profiles o={o} />
    </div>
  );
}

const GREEK_CURVES = [
  { value: "value", label: "Value" },
  { value: "delta", label: "Delta" },
  { value: "gamma", label: "Gamma" },
  { value: "vega", label: "Vega" },
  { value: "theta_day", label: "Theta" },
] as const;
type Curve = (typeof GREEK_CURVES)[number]["value"];

function Profiles({ o }: { o: PriceOut }) {
  const [curve, setCurve] = useState<Curve>("value");
  const c = o.curves;
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      if (curve === "value")
        return [
          { type: "scatter", mode: "lines", name: "Payoff at expiry", x: c.spot, y: c.payoff_at_expiry, line: { color: t.text3, width: 1.4, dash: "dash" }, hovertemplate: "expiry %{y:,.2f}<extra></extra>" },
          { type: "scatter", mode: "lines", name: "Value today", x: c.spot, y: c.value_today, line: { color: t.categorical[0], width: 2.2 }, fill: "tonexty", fillcolor: withAlpha(t.categorical[0], 0.08), hovertemplate: "today <b>%{y:,.2f}</b><extra>S %{x:,.2f}</extra>" },
        ] as Data[];
      const y = curve === "vega" ? c.vega.map((v) => v / 100) : c[curve];
      return [{ type: "scatter", mode: "lines", name: curve, x: c.spot, y, line: { color: t.categorical[0], width: 2 }, hovertemplate: `<b>%{y:,.4f}</b><extra>S %{x:,.2f}</extra>` }] as Data[];
    },
    [c, curve],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      hovermode: "x unified",
      xaxis: { title: { text: "Spot" }, tickformat: ",.0f" },
      yaxis: { side: "right", tickformat: curve === "gamma" ? ".4f" : ",.2f" },
      shapes: [
        { type: "line", yref: "paper", y0: 0, y1: 1, x0: o.inputs.S, x1: o.inputs.S, line: { color: t.text2, width: 1, dash: "dot" } },
        { type: "line", yref: "paper", y0: 0, y1: 1, x0: o.inputs.K, x1: o.inputs.K, line: { color: t.ruleStrong, width: 1 } },
      ],
      annotations: [
        { x: o.inputs.S, yref: "paper", y: 1, text: "S", showarrow: false, xanchor: "left", yanchor: "top", xshift: 4, font: { size: 10, color: t.text2 } },
        { x: o.inputs.K, yref: "paper", y: 0.9, text: "K", showarrow: false, xanchor: "left", yanchor: "top", xshift: 4, font: { size: 10, color: t.text3 } },
      ],
      margin: { l: 16, r: 8, t: 36, b: 44 },
    }),
    [curve, o.inputs.S, o.inputs.K],
  );
  return (
    <div>
      <div className="vx-toolbar">
        <span className="vx-side-title">Across spot · ±3.5 standard deviations</span>
        <SegmentedControl size="sm" ariaLabel="Profile" options={[...GREEK_CURVES]} value={curve} onChange={setCurve} />
      </div>
      <Chart data={data} layout={layout as never} height={300} />
    </div>
  );
}

export type { Greeks };
