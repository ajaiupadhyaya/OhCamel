/**
 * CALC (POST /api/options/price): BSM price and every greek, implied vol from a price, the
 * put-call parity check and value / greek profiles across spot. Inputs are seeded from real
 * data: spot = the underlying's last close, σ = its 30-day implied (Cboe index) or 21-day
 * realized vol, r = FRED 3-month bill (server-side), falling back visibly to the 2-year
 * Treasury when the bill is unavailable (offline), or a rate typed in.
 */
import { useEffect, useMemo, useState } from "react";
import { Field, NumberField, Panel, SegmentedControl } from "../../components";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { isDataUnavailable } from "../../lib/api";
import { fmtDate, fmtNum, fmtPct, fmtPctPoints, fmtSci } from "../../lib/format";
import { useDebounced } from "../../lib/hooks";
import { useApiPost, useApiQuery } from "../../lib/query";
import { roundTo } from "./derive";
import { INFO } from "./info";
import { GreekGrid, KV, Readline } from "./shared";
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
  if (iv != null) return { S, sigma: iv, sigmaSource: `${r.vrp!.implied_source.split(" (")[0].toUpperCase()} · 30D IMPLIED`, asOf: r.as_of };
  if (rv != null) return { S, sigma: rv, sigmaSource: "CC RV · 21D", asOf: r.as_of };
  return null;
}

export function CalculatorTab({ ticker, seed, seedLoading }: { ticker: string; seed: CalcSeed | null; seedLoading: boolean }) {
  if (!seed) {
    return (
      <Panel title="Calc · BSM" loading={seedLoading} skeletonHeight={420}>
        <Absent reason="NO PRICE HISTORY TO SEED S AND σ" source={`/api/options/realized/${ticker}`} />
      </Panel>
    );
  }
  return <Calculator key={`${ticker}-${seed.S}`} ticker={ticker} seed={seed} />;
}

function Calculator({ ticker, seed }: { ticker: string; seed: CalcSeed }) {
  const roundK = (s: number) => (s >= 200 ? Math.round(s / 5) * 5 : s >= 20 ? Math.round(s) : roundTo(s, 1));
  const [type, setType] = useState<"C" | "P">("C");
  const [mode, setMode] = useState<Mode>("price");
  const [S, setS] = useState(roundTo(seed.S, 2));
  const [K, setK] = useState(roundK(seed.S));
  const [days, setDays] = useState(30);
  const [sigma, setSigma] = useState(roundTo(seed.sigma, 4));
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
    const base = { S, K, T: days / 365, q, type, ...(r != null ? { r: roundTo(r, 6) } : {}) };
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
    if (m === "iv" && px == null && res.data) setPx(roundTo(res.data.result.price, 2));
    setMode(m);
  };

  const rNote =
    rMode === "bill"
      ? [{ k: "r", v: "FRED DGS3MO · BOND-EQUIV → CONTINUOUS (SERVER)" }]
      : rMode === "t2y"
        ? [
            { k: "DGS2", v: y2?.latest != null ? `${fmtPctPoints(y2.latest, 2)} · ${fmtDate(y2.date).toUpperCase()}` : "LOADING" },
            { k: "r = 2 LN(1 + y/2)", v: fmtPct(r2y, 3) },
          ]
        : [{ k: "r", v: "MANUAL · CONTINUOUS" }];

  return (
    <div className="vx-calc">
      <Panel
        title={
          <>
            Inputs · {ticker}
            <Note n={1} to="bsm" />
          </>
        }
        asOf={seed.asOf}
        className="vx-calc-inputs"
      >
        <div className="stack">
          <div className="row-wrap">
            <SegmentedControl ariaLabel="Option type" options={[{ value: "C", label: "CALL" }, { value: "P", label: "PUT" }]} value={type} onChange={setType} />
            <SegmentedControl ariaLabel="Solve for" options={[{ value: "price", label: <>PRICE ← <span className="vx-sym">σ</span></> }, { value: "iv", label: <><span className="vx-sym">σ</span> ← PRICE</> }]} value={mode} onChange={switchMode} />
          </div>
          <div className="vx-calc-fields">
            <NumberField label="S" value={S} onChange={setS} min={0.01} step={1} digits={2} hint={`CLOSE ${fmtDate(seed.asOf).toUpperCase()}`} />
            <NumberField label="K" value={K} onChange={setK} min={0.01} step={K >= 200 ? 5 : 1} digits={2} />
            <NumberField label="Days" value={days} onChange={(v) => setDays(Math.max(1, Math.round(v)))} min={1} max={3650} step={1} digits={0} unit="D" hint={`T ${fmtNum(days / 365, 4)}Y · ACT/365`} />
            {mode === "price" ? (
              <NumberField label={<span className="vx-sym">σ</span>} value={sigma} onChange={setSigma} percent min={0.005} max={5} step={0.005} digits={2} hint={`SEED ${seed.sigmaSource} ${fmtPct(seed.sigma, 1)}`} />
            ) : (
              <NumberField label="Price" value={px} onChange={setPx} min={0.0001} step={0.05} digits={4} info={INFO.iv} />
            )}
            <NumberField label={<span className="vx-sym">q</span>} value={q} onChange={setQ} percent min={-0.5} max={1} step={0.0025} digits={2} hint="CONTINUOUS" />
          </div>
          <Field label={<span className="vx-sym">r</span>}>
            <div className="row-wrap">
              <SegmentedControl
                size="sm"
                ariaLabel="Rate source"
                options={[
                  { value: "bill", label: "3M BILL" },
                  { value: "t2y", label: "UST 2Y" },
                  { value: "manual", label: "MANUAL" },
                ]}
                value={rMode}
                onChange={(v) => {
                  setTouched(true);
                  setFellBack(false);
                  setRMode(v);
                }}
              />
              {rMode === "manual" && <NumberField ariaLabel="Manual rate" value={rManual} onChange={setRManual} percent min={-0.2} max={1} step={0.0025} digits={3} width={120} />}
            </div>
          </Field>
          <Readline items={[...rNote, ...(fellBack && rMode === "t2y" ? [{ k: "DGS3MO", v: "UNAVAILABLE · UST 2Y STANDS IN", tone: "loss" }] : [])]} />
        </div>
      </Panel>

      <Panel<PriceOut>
        title={`${type === "C" ? "Call" : "Put"} · BSM`}
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
      <Readline
        items={[
          { k: "S", v: fmtNum(o.inputs.S, 2) },
          { k: "K", v: fmtNum(o.inputs.K, 2) },
          { k: "T", v: `${fmtNum(o.inputs.T * 365, 0)}D` },
          { k: "σ", v: fmtPct(o.inputs.sigma, 2) },
          { k: "r", v: fmtPct(o.inputs.r, 3) },
          { k: "q", v: fmtPct(o.inputs.q, 2) },
        ]}
      />
      <div className="vx-calc-hero">
        <div>
          <div className="vx-calc-k">{mode === "iv" ? "IMPLIED VOL" : "FAIR VALUE"}</div>
          <div className="vx-calc-price num">{mode === "iv" ? fmtPct(o.implied_vol, 2) : fmtNum(g.price, 4)}</div>
          <div className="vx-calc-sub num">
            {mode === "iv" ? `REPRICES ${fmtNum(o.inputs.price, 4)}` : `${fmtNum(g.price * 100, 2)} / CONTRACT`} · F {fmtNum(o.forward, 2)} · D {fmtNum(o.discount, 5)}
          </div>
        </div>
        <KV
          rows={[
            { label: "Call", value: fmtNum(o.call.price, 4) },
            { label: "Put", value: fmtNum(o.put.price, 4) },
            { label: "Parity gap", value: fmtSci(o.parity_check.gap, 1), info: { title: "Put-call parity", text: "C − P − (S e^{−qT} − K e^{−rT}); zero to machine precision.", formula: "C - P = S e^{-qT} - K e^{-rT}", reference: "Stoll (1969), J. Finance 24(5)" } },
          ]}
        />
      </div>
      <GreekGrid
        cells={[
          { label: "Delta", value: fmtNum(g.delta, 4), info: INFO.delta },
          { label: "Gamma", value: fmtNum(g.gamma, 5), info: INFO.gamma },
          { label: "Vega", value: fmtNum(g.vega / 100, 4), info: INFO.vega, caption: "PER VOL PT" },
          { label: "Theta", value: fmtNum(g.theta_day, 4), info: INFO.theta, caption: `PER DAY · ${fmtNum(g.theta, 2)}/Y` },
          { label: "Rho", value: fmtNum(g.rho / 100, 4), info: INFO.rho, caption: "PER 1% RATE" },
          { label: "Vanna", value: fmtNum(g.vanna, 4), info: INFO.vanna },
          { label: "Volga", value: fmtNum(g.volga, 3), info: INFO.volga },
          { label: "Charm", value: fmtNum(g.charm_day, 5), info: INFO.charm, caption: "PER DAY" },
          { label: "d₁", sym: true, value: fmtNum(g.d1, 4), info: { title: "d₁ and d₂", text: "N(d₂) is the risk-neutral probability the option finishes in the money; e^{-qT}N(d₁) is the call delta.", formula: "d_{1,2} = \\frac{\\ln(S/K) + (r - q \\pm \\tfrac12\\sigma^2)T}{\\sigma\\sqrt T}" } },
          { label: "d₂", sym: true, value: fmtNum(g.d2, 4), caption: "N(d₂) = P(ITM) · Q" },
        ]}
      />
      <Profiles o={o} />
    </div>
  );
}

const GREEK_CURVES = [
  { value: "value", label: "VALUE" },
  { value: "delta", label: "DELTA" },
  { value: "gamma", label: "GAMMA" },
  { value: "vega", label: "VEGA" },
  { value: "theta_day", label: "THETA" },
] as const;
type Curve = (typeof GREEK_CURVES)[number]["value"];

function Profiles({ o }: { o: PriceOut }) {
  const [curve, setCurve] = useState<Curve>("value");
  const c = o.curves;
  const series = useMemo<XYSeries[]>(() => {
    if (curve === "value")
      return [
        { name: "EXPIRY", y: c.payoff_at_expiry, tone: "ink3", dash: "dash" },
        { name: "TODAY", y: c.value_today, tone: "ink", width: 1.75 },
      ];
    return [{ name: curve === "theta_day" ? "THETA" : curve.toUpperCase(), y: curve === "vega" ? c.vega.map((v) => v / 100) : c[curve], tone: "ink", width: 1.5 }];
  }, [c, curve]);
  return (
    <div>
      <div className="vx-toolbar">
        <span className="vx-side-title">Across spot · ±3.5<span className="vx-sym">σ</span></span>
        <SegmentedControl size="sm" ariaLabel="Profile" options={[...GREEK_CURVES]} value={curve} onChange={setCurve} />
      </div>
      <XYChart
        x={c.spot}
        series={series}
        xFormat="num"
        yFormat="num"
        digits={curve === "gamma" ? 4 : 2}
        height={300}
        xTitle="Spot"
        vlines={[
          { at: o.inputs.S, label: "S", tone: "ink2", dash: "dot" },
          { at: o.inputs.K, label: "K", tone: "ink3", dash: "solid" },
        ]}
        ariaLabel={`Option ${curve} across spot`}
      />
    </div>
  );
}

export type { Greeks };
