/**
 * Bonds — POST /api/macro/bond on an explicit "Run": clean/dirty price ⇄ yield, Macaulay &
 * modified duration, convexity, DV01 (per 100 and on a notional), cash-flow schedule,
 * price–yield curve with duration / convexity approximations, and — when the Treasury curve
 * is reachable — curve fair value, rich/cheap, Z-spread, effective duration and key-rate
 * durations (Ho 1992). Inputs are seeded from the latest 10-year Treasury yield (FRED DGS10).
 */
import { useEffect, useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, Field, NumberField, Panel, SegmentedControl, StatGrid, StatTile, Toggle } from "../../components";
import { Icon } from "../../components/Icon";
import { DataUnavailableError } from "../../lib/api";
import { fmtCurrency, fmtDate, fmtNum, fmtPctPoints } from "../../lib/format";
import { useApiPost, useApiQuery } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { Bars, KV, MethodCard, tenorLabel } from "./shared";
import type { BondIn, BondOut, FredExplorer } from "./types";

type PxMode = "yield" | "price" | "curve";
type TermMode = "years" | "maturity";

export function BondsTab() {
  const dgs10 = useApiQuery<FredExplorer>("/macro/series", { ids: "DGS10", start: "2025-01-01" }, { staleTime: Infinity });
  const seed = dgs10.data?.summary?.DGS10;
  const seedFailed = dgs10.isError;

  const [coupon, setCoupon] = useState(4.25);
  const [termMode, setTermMode] = useState<TermMode>("years");
  const [years, setYears] = useState(10);
  const [maturity, setMaturity] = useState("");
  const [freq, setFreq] = useState<"1" | "2" | "4" | "12">("2");
  const [settlement, setSettlement] = useState("");
  const [pxMode, setPxMode] = useState<PxMode>("yield");
  const [yieldPct, setYieldPct] = useState<number | null>(null);
  const [price, setPrice] = useState(100);
  const [notional, setNotional] = useState(1_000_000);
  const [useCurve, setUseCurve] = useState(true);
  const [committed, setCommitted] = useState<BondIn | null>(null);

  const body = useMemo<BondIn | null>(() => {
    const b: BondIn = { coupon, freq: +freq as BondIn["freq"], use_curve: useCurve, notional };
    if (termMode === "years") b.years = years;
    else if (maturity) b.maturity = maturity;
    else return null;
    if (settlement) b.settlement = settlement;
    if (pxMode === "yield") {
      if (yieldPct == null) return null;
      b.yield_pct = yieldPct;
    } else if (pxMode === "price") b.price = price;
    return b;
  }, [coupon, freq, useCurve, notional, termMode, years, maturity, settlement, pxMode, yieldPct, price]);

  // Seed once from the latest 10-year yield: yield = DGS10, coupon = DGS10 rounded down to 1/8 (how new notes are auctioned).
  useEffect(() => {
    if (committed || yieldPct != null) return;
    if (seed?.latest != null) {
      const y = +seed.latest.toFixed(3);
      const c = Math.floor(seed.latest * 8) / 8;
      setYieldPct(y);
      setCoupon(c);
      setCommitted({ coupon: c, years: 10, freq: 2, use_curve: true, notional: 1_000_000, yield_pct: y });
    } else if (seedFailed) {
      setPxMode("price");
      setCommitted({ coupon: 4.25, years: 10, freq: 2, use_curve: true, notional: 1_000_000, price: 100 });
    }
  }, [seed, seedFailed]); // eslint-disable-line react-hooks/exhaustive-deps

  const res = useApiPost<BondOut>("/macro/bond", committed, { enabled: committed != null });
  const canon = (b: BondIn | null) => (b ? JSON.stringify(Object.keys(b).sort().map((k) => [k, b[k as keyof BondIn]])) : "null");
  const dirty = canon(body) !== canon(committed);
  const curveOff = !!res.data && !res.data.curve && !!committed?.use_curve && !res.isFetching;

  return (
    <div className="stack">
      <div className="mc-bond-layout">
        <aside className="mc-bond-form oc-panel">
          <div className="eyebrow">Bond</div>
          <NumberField label="Coupon" value={coupon} onChange={setCoupon} unit="% / yr" min={0} max={50} step={0.125} info={{ text: "Annual coupon rate, paid in equal instalments at the chosen frequency." }} />
          <Field label="Maturity">
            <SegmentedControl size="sm" options={[{ value: "years", label: "Years" }, { value: "maturity", label: "Date" }]} value={termMode} onChange={setTermMode} ariaLabel="Maturity input" />
          </Field>
          {termMode === "years" ? (
            <NumberField value={years} onChange={setYears} unit="years" min={0.25} max={100} step={1} />
          ) : (
            <input type="date" className="input num" value={maturity} onChange={(e) => setMaturity(e.target.value)} aria-label="Maturity date" />
          )}
          <Field label="Coupons per year">
            <SegmentedControl size="sm" options={[{ value: "1", label: "1" }, { value: "2", label: "2" }, { value: "4", label: "4" }, { value: "12", label: "12" }]} value={freq} onChange={setFreq} ariaLabel="Coupon frequency" />
          </Field>
          <Field label="Settlement" hint="Blank = next business day (T+1).">
            <input type="date" className="input num" value={settlement} onChange={(e) => setSettlement(e.target.value)} aria-label="Settlement date" />
          </Field>
          <div className="mc-form-rule" />
          <Field label="Price the bond from" info={INFO.ytm}>
            <SegmentedControl size="sm" options={[{ value: "yield", label: "Yield" }, { value: "price", label: "Price" }, { value: "curve", label: "Curve" }]} value={pxMode} onChange={setPxMode} ariaLabel="Pricing input" />
          </Field>
          {pxMode === "yield" && (
            <NumberField
              label="Yield to maturity"
              value={yieldPct}
              onChange={setYieldPct}
              unit="%"
              min={-49}
              max={99}
              step={0.05}
              hint={seed ? <>Seeded with the 10-year Treasury, {fmtPctPoints(seed.latest)} on {fmtDate(seed.date)} (FRED DGS10).</> : dgs10.isLoading ? "Loading the latest 10-year yield…" : undefined}
            />
          )}
          {pxMode === "price" && <NumberField label="Clean price" value={price} onChange={setPrice} unit="per 100" min={0.01} step={0.25} info={INFO.dirty} />}
          {pxMode === "curve" && <div className="subtle small">Priced at fair value on today's Treasury zero curve (needs the full curve from FRED).</div>}
          <NumberField label="Notional" value={notional} onChange={setNotional} unit="$" min={1} step={100000} />
          <Toggle label="Curve analytics (fair value, Z-spread, KRDs)" checked={useCurve} onChange={setUseCurve} />
          <button type="button" className="btn btn-primary mc-run" disabled={!body || (!dirty && !!res.data)} onClick={() => body && setCommitted(body)}>
            <Icon name="arrow-right" size={15} /> {res.isFetching ? "Running…" : dirty || !res.data ? "Run" : "Up to date"}
          </button>
          {dirty && res.data && <div className="subtle small">Inputs changed — press Run to reprice.</div>}
        </aside>

        <div className="stack">
          <Panel<BondOut> title="Price, yield & risk" subtitle="What the bond costs and how sensitive it is to interest rates. Duration ≈ % price change per 1-point yield move; DV01 is the dollar change per 0.01%." info={INFO.modified} query={res} loading={res.isLoading || (!committed && !seedFailed)} skeletonHeight={180} notes={[]} provenance={[]}>
            {(d) => <Headline d={d} />}
          </Panel>
          <Panel<BondOut> title="Price–yield curve" subtitle="The exact price at yields ±300 bp, and how well duration alone (a straight line) and duration + convexity approximate it. The gap between them is convexity at work." info={INFO.convexity} query={res} loading={res.isLoading || !committed} skeletonHeight={320} notes={[]} provenance={[]}>
            {(d) => <PriceYield d={d} />}
          </Panel>
        </div>
      </div>

      {curveOff ? (
        <Panel title="Curve analytics & key-rate durations" subtitle="Fair value on the Treasury zero curve, rich/cheap, Z-spread, effective duration and where along the curve the rate risk sits. The price, yield and risk measures above don't need the curve." info={INFO.krd} error={curveError(res.data!)} compact notes={[]} />
      ) : (
        <div className="grid-3">
          <Panel<BondOut> title="Curve analytics" subtitle="The bond against today's Treasury zero curve: fair value, how rich or cheap your price is, and the spread you earn over Treasuries." info={INFO.zspread} query={res} loading={res.isLoading || !committed} skeletonHeight={260} notes={[]} provenance={[]}>
            {(d) => (d.curve ? <CurveStats d={d} /> : <div className="subtle small">Curve analytics are switched off — turn them on and Run.</div>)}
          </Panel>
          <Panel<BondOut> title="Key-rate durations" subtitle="Where along the curve the bond's rate risk sits: price sensitivity to a 1 bp move at each maturity alone. Bars sum to effective duration." info={INFO.krd} query={res} loading={res.isLoading || !committed} skeletonHeight={260} span={2} notes={[]} provenance={[]}>
            {(d) => (d.curve ? <KRD d={d} /> : <div className="subtle small">Curve analytics are switched off.</div>)}
          </Panel>
        </div>
      )}

      <div className="grid-3">
        <Panel<BondOut> title="Cash flows" subtitle="Every coupon and the final principal, with what each is worth today at the bond's yield. Distant cash flows shrink the most — that's duration." info={INFO.macaulay} query={res} loading={res.isLoading || !committed} skeletonHeight={260} span={2}>
          {(d) => <Cashflows d={d} />}
        </Panel>
        <MethodCard
          title="One yield, many sensitivities"
          formulas={["P = \\sum_{k} \\frac{CF_k}{(1+y/m)^{m t_k}}", "\\frac{\\Delta P}{P} \\approx -D_{\\text{mod}}\\,\\Delta y + \\tfrac12 C\\,(\\Delta y)^2"]}
          refs={["SIFMA Standard Formulas; Fabozzi, Bond Markets, Analysis & Strategies", "Ho (1992), J. Fixed Income 2(2) — key-rate durations", "Brent (1973) — yield solver"]}
        >
          A bond is a bundle of dated cash flows. Discounting them at one yield gives the price; differentiating the price with respect to that yield gives duration and convexity. Discounting on the full Treasury zero curve instead gives a fair value, and bumping the curve one maturity at a time shows where the risk lives.
        </MethodCard>
      </div>
    </div>
  );
}

function curveError(d: BondOut) {
  const n = (Array.isArray(d.notes) ? d.notes : []).find((x) => x.startsWith("curve-based analytics unavailable"));
  return new DataUnavailableError(n ? n.replace(/^curve-based analytics unavailable:\s*/, "") : "Treasury curve unavailable", "/api/macro/bond");
}

function Headline({ d }: { d: BondOut }) {
  const r = d.risk;
  return (
    <div className="stack">
      <StatGrid min={150}>
        <StatTile label="Clean price" value={fmtNum(d.clean_price, 3)} info={INFO.dirty} caption={d.bond.price_source === "curve" ? "curve fair value" : "per 100 face"} />
        <StatTile label="YTM" value={fmtPctPoints(d.ytm_pct, 3)} info={INFO.ytm} caption={`compounded ${d.bond.freq}×/yr`} />
        <StatTile label="Dirty price" value={fmtNum(d.dirty_price, 3)} info={INFO.dirty} caption={`accrued ${fmtNum(d.accrued, 3)}`} />
        <StatTile label="Macaulay dur." value={`${fmtNum(r.macaulay_duration, 2)} y`} info={INFO.macaulay} />
        <StatTile label="Modified dur." value={fmtNum(r.modified_duration, 2)} info={INFO.modified} caption="% per 1 pp" />
        <StatTile label="Convexity" value={fmtNum(r.convexity, 1)} info={INFO.convexity} />
        <StatTile label="DV01" value={fmtNum(r.dv01, 4)} info={INFO.dv01} caption="per 100 face" />
        <StatTile label="DV01 · notional" value={fmtCurrency(r.dv01_notional, { digits: 0 })} info={INFO.dv01} caption={`${fmtCurrency(r.notional, { compact: true })} face`} />
      </StatGrid>
      <div className="subtle small">
        {fmtNum(d.bond.coupon_pct, 3)}% coupon, matures {fmtDate(d.bond.maturity)}, settles {fmtDate(d.bond.settlement)}; <span className="num">{d.cashflows.length}</span> remaining cash flows.
      </div>
    </div>
  );
}

function PriceYield({ d }: { d: BondOut }) {
  const py = d.price_yield;
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      { type: "scatter", mode: "lines", name: "Exact price", x: py.yield_pct, y: py.price, line: { color: t.categorical[0], width: 2.4 }, hovertemplate: "<b>Exact</b> %{y:.3f}<extra></extra>" } as Data,
      { type: "scatter", mode: "lines", name: "Duration only", x: py.yield_pct, y: py.duration_approx, line: { color: t.categorical[1], width: 1.4, dash: "dash" }, hovertemplate: "<b>Duration</b> %{y:.3f}<extra></extra>" } as Data,
      { type: "scatter", mode: "lines", name: "Duration + convexity", x: py.yield_pct, y: py.duration_convexity_approx, line: { color: t.categorical[2], width: 1.4, dash: "dot" }, hovertemplate: "<b>Dur + conv</b> %{y:.3f}<extra></extra>" } as Data,
      { type: "scatter", mode: "markers", name: "Today", x: [d.ytm_pct], y: [d.dirty_price], marker: { size: 10, color: t.surface, line: { color: t.text, width: 2 } }, hovertemplate: "<b>Today</b> %{y:.3f} at %{x:.3f}%<extra></extra>" } as Data,
    ],
    [py, d.ytm_pct, d.dirty_price],
  );
  const layout = useMemo(() => ({ hovermode: "x unified", xaxis: { title: { text: "Yield to maturity" }, ticksuffix: "%", hoverformat: ".2f", showspikes: true }, yaxis: { title: { text: "Dirty price per 100" }, side: "right", tickformat: ",.0f" }, margin: { l: 16, r: 8, t: 36, b: 44 } }) as any, []);
  return <Chart data={data} layout={layout} height={300} ariaLabel="Price-yield curve" />;
}

function CurveStats({ d }: { d: BondOut }) {
  const c = d.curve!;
  return (
    <div className="stack">
      <StatGrid min={130}>
        <StatTile label="Fair value (clean)" value={fmtNum(c.fair_clean, 3)} info={INFO.fair} caption={`curve of ${fmtDate(c.date)}`} />
        <StatTile label="Rich / cheap" value={fmtNum(c.rich_cheap, 3, { signed: true })} info={INFO.fair} caption={c.rich_cheap > 0 ? "rich (above fair)" : c.rich_cheap < 0 ? "cheap (below fair)" : "at fair value"} />
        <StatTile label="Z-spread" value={`${fmtNum(c.z_spread_bp, 1, { signed: true })} bp`} info={INFO.zspread} />
      </StatGrid>
      <KV
        rows={[
          { k: "Effective duration", v: fmtNum(c.effective_duration, 3), info: INFO.effdur },
          { k: "Effective convexity", v: fmtNum(c.effective_convexity, 1), info: INFO.convexity },
          { k: "Sum of KRDs", v: fmtNum(c.krd_sum, 3), info: INFO.krd, muted: true },
        ]}
      />
    </div>
  );
}

function KRD({ d }: { d: BondOut }) {
  const k = d.curve!.key_rate_durations;
  const data = useMemo(
    () => (t: Tokens): Data[] => [
      {
        type: "bar",
        x: k.map((r) => tenorLabel(r.tenor)),
        y: k.map((r) => r.krd),
        customdata: k.map((r) => r.krd01_notional),
        marker: { color: t.categorical[0] },
        hovertemplate: "%{x}: <b>%{y:.3f}</b> yrs<br>$%{customdata:,.0f} per bp on notional<extra></extra>",
      } as Data,
    ],
    [k],
  );
  const layout = useMemo(() => ({ showlegend: false, xaxis: { type: "category", showgrid: false, showspikes: false, title: { text: "Key tenor" } }, yaxis: { tickformat: ".2f", side: "right", title: { text: "KRD" } }, margin: { l: 16, r: 8, t: 12, b: 44 } }) as any, []);
  return <Chart data={data} layout={layout} height={260} ariaLabel="Key-rate durations" />;
}

function Cashflows({ d }: { d: BondOut }) {
  const cf = d.cashflows;
  const series = [
    { name: "Cash flow", x: cf.map((r) => r.date), y: cf.map((r) => r.cashflow) },
    { name: "Present value", x: cf.map((r) => r.date), y: cf.map((r) => r.pv) },
  ];
  const big = cf.length > 0 && cf[cf.length - 1].cashflow > 10 * (cf[0]?.cashflow || 1);
  return (
    <>
      <Bars series={series} tick=",.4~g" hover=",.3f" height={260} layout={{ xaxis: { type: "date" }, yaxis: { type: big ? "log" : "linear", title: { text: big ? "per 100 face (log)" : "per 100 face" } }, bargap: 0.15 }} />
      <div className="subtle small">Present values discount each cash flow at the yield to maturity; they add up to the dirty price ({fmtNum(d.dirty_price, 3)}).</div>
    </>
  );
}
