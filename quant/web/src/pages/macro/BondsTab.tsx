/**
 * BONDS — POST /api/macro/bond on RUN: clean / dirty price ⇄ yield, Macaulay and modified
 * duration, convexity, DV01 (per 100 and on a notional), the cash-flow schedule, the
 * price–yield curve against its duration and duration + convexity approximations, and — with
 * the Treasury curve — fair value, rich / cheap, Z-spread, effective duration and key-rate
 * durations (Ho 1992). The yield is seeded from the latest 10-year Treasury (FRED DGS10).
 */
import { useEffect, useMemo, useState } from "react";
import { BarChart, DataTable, Field, NumberField, Panel, SegmentedControl, StatGrid, StatTile, Toggle, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { fmtCurrency, fmtDate, fmtNum, fmtPctPoints } from "../../lib/format";
import { useApiPost, useApiQuery } from "../../lib/query";
import { alignNumeric, tenorLabel } from "./derive";
import { INFO } from "./info";
import { KV, Readline } from "./shared";
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
      const y = Math.round(seed.latest * 1000) / 1000;
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
  const pending = res.isLoading || !committed;
  const curveNote = res.data && !res.data.curve && committed?.use_curve && !res.isFetching ? curveReason(res.data) : null;

  return (
    <div className="stack">
      <div className="mc-bond">
        <aside className="mc-bond-form">
          <h3 className="mc-sub">BOND</h3>
          <NumberField label="Coupon" value={coupon} onChange={setCoupon} unit="% / YR" min={0} max={50} step={0.125} />
          <Field label="Maturity">
            <SegmentedControl size="sm" options={[{ value: "years", label: "YEARS" }, { value: "maturity", label: "DATE" }]} value={termMode} onChange={setTermMode} ariaLabel="Maturity input" />
          </Field>
          {termMode === "years" ? <NumberField value={years} onChange={setYears} unit="Y" min={0.25} max={100} step={1} ariaLabel="Years to maturity" /> : <input type="date" className="input num" value={maturity} onChange={(e) => setMaturity(e.target.value)} aria-label="Maturity date" />}
          <Field label="Coupons / yr">
            <SegmentedControl size="sm" options={[{ value: "1", label: "1" }, { value: "2", label: "2" }, { value: "4", label: "4" }, { value: "12", label: "12" }]} value={freq} onChange={setFreq} ariaLabel="Coupon frequency" />
          </Field>
          <Field label="Settle · blank T+1">
            <input type="date" className="input num" value={settlement} onChange={(e) => setSettlement(e.target.value)} aria-label="Settlement date" />
          </Field>
          <Field label="Price from" info={INFO.ytm}>
            <SegmentedControl size="sm" options={[{ value: "yield", label: "YIELD" }, { value: "price", label: "PRICE" }, { value: "curve", label: "CURVE" }]} value={pxMode} onChange={setPxMode} ariaLabel="Pricing input" />
          </Field>
          {pxMode === "yield" && <NumberField label="YTM" value={yieldPct} onChange={setYieldPct} unit="%" min={-49} max={99} step={0.05} hint={seed ? `SEED DGS10 ${fmtPctPoints(seed.latest)} · ${fmtDate(seed.date, "short").toUpperCase()}` : undefined} />}
          {pxMode === "price" && <NumberField label="Clean" value={price} onChange={setPrice} unit="/ 100" min={0.01} step={0.25} info={INFO.dirty} />}
          {pxMode === "curve" && <div className="mc-none num">FAIR VALUE · UST ZERO CURVE</div>}
          <NumberField label="Notional" value={notional} onChange={setNotional} unit="$" min={1} step={100000} />
          <Toggle label="Curve analytics" checked={useCurve} onChange={setUseCurve} />
          <button type="button" className={`btn btn-sm mc-run ${dirty || !res.data ? "btn-primary" : ""}`} disabled={!body || (!dirty && !!res.data)} onClick={() => body && setCommitted(body)}>
            {res.isFetching ? "RUNNING" : dirty || !res.data ? "RUN" : "CURRENT"}
          </button>
        </aside>

        <div className="stack">
          <Panel<BondOut>
            title={
              <>
                PRICE · YIELD · RISK
                <Note n={1} to="bonds" />
              </>
            }
            query={res}
            loading={pending && !seedFailed}
            skeletonHeight={180}
            notes={[]}
            provenance={[]}
          >
            {(d) => <Headline d={d} />}
          </Panel>
          <Panel<BondOut> title="PRICE–YIELD · ±300 BP" query={res} loading={pending} skeletonHeight={300} notes={[]} provenance={[]}>
            {(d) => <PriceYield d={d} />}
          </Panel>
        </div>
      </div>

      <div className="grid-3">
        <Panel<BondOut> title="CURVE · FAIR VALUE · Z-SPREAD" query={res} loading={pending} skeletonHeight={240} notes={[]} provenance={[]}>
          {(d) => (d.curve ? <CurveStats d={d} /> : <Absent reason={curveNote ?? "CURVE ANALYTICS OFF"} source="/api/macro/bond · use_curve" />)}
        </Panel>
        <Panel<BondOut>
          title={
            <>
              KEY-RATE DURATION
              <Note n={2} to="bonds" />
            </>
          }
          query={res}
          loading={pending}
          skeletonHeight={240}
          span={2}
          notes={[]}
          provenance={[]}
        >
          {(d) => (d.curve ? <KRD d={d} /> : <Absent reason={curveNote ?? "CURVE ANALYTICS OFF"} source="/api/macro/bond · use_curve" />)}
        </Panel>
      </div>

      <Panel<BondOut> title="CASH FLOWS · PER 100" query={res} loading={pending} skeletonHeight={240} flush>
        {(d) => <Cashflows d={d} />}
      </Panel>
    </div>
  );
}

function curveReason(d: BondOut): string {
  const n = (Array.isArray(d.notes) ? d.notes : []).find((x) => x.startsWith("curve-based analytics unavailable"));
  return n ? `CURVE UNAVAILABLE · ${n.replace(/^curve-based analytics unavailable:\s*/, "")}` : "CURVE UNAVAILABLE";
}

function Headline({ d }: { d: BondOut }) {
  const r = d.risk;
  return (
    <>
      <Readline
        items={[
          { k: "CPN", v: `${fmtNum(d.bond.coupon_pct, 3)}%` },
          { k: "MAT", v: fmtDate(d.bond.maturity).toUpperCase() },
          { k: "SETTLE", v: fmtDate(d.bond.settlement).toUpperCase() },
          { k: "FLOWS", v: fmtNum(d.cashflows.length, 0) },
          { k: "PX FROM", v: d.bond.price_source.toUpperCase() },
        ]}
      />
      <StatGrid min={130}>
        <StatTile size="sm" label="CLEAN" info={INFO.dirty} value={fmtNum(d.clean_price, 3)} caption="PER 100" />
        <StatTile size="sm" label="YTM" info={INFO.ytm} value={fmtPctPoints(d.ytm_pct, 3)} caption={`${d.bond.freq}×/YR`} />
        <StatTile size="sm" label="DIRTY" info={INFO.dirty} value={fmtNum(d.dirty_price, 3)} caption={`AI ${fmtNum(d.accrued, 3)}`} />
        <StatTile size="sm" label="D MAC" info={INFO.macaulay} value={`${fmtNum(r.macaulay_duration, 2)} Y`} />
        <StatTile size="sm" label="D MOD" info={INFO.modified} value={fmtNum(r.modified_duration, 2)} caption="% / 1 PP" />
        <StatTile size="sm" label="CONVEXITY" info={INFO.convexity} value={fmtNum(r.convexity, 1)} />
        <StatTile size="sm" label="DV01" info={INFO.dv01} value={fmtNum(r.dv01, 4)} caption="PER 100" />
        <StatTile size="sm" label="DV01 · NOTIONAL" info={INFO.dv01} value={fmtCurrency(r.dv01_notional, { digits: 0 })} caption={`${fmtCurrency(r.notional, { compact: true })} FACE`} />
      </StatGrid>
    </>
  );
}

function PriceYield({ d }: { d: BondOut }) {
  const py = d.price_yield;
  const a = useMemo(
    () =>
      alignNumeric([
        { x: py.yield_pct, y: py.price },
        { x: py.yield_pct, y: py.duration_approx },
        { x: py.yield_pct, y: py.duration_convexity_approx },
        { x: [d.ytm_pct], y: [d.dirty_price] },
      ]),
    [py, d.ytm_pct, d.dirty_price],
  );
  return (
    <XYChart
      x={a.x}
      series={[
        { name: "EXACT", y: a.ys[0], tone: "ink", span: true },
        { name: "DUR", y: a.ys[1], tone: "ink2", dash: "dash", span: true },
        { name: "DUR+CONV", y: a.ys[2], tone: "ink3", dash: "dot", span: true },
        { name: "NOW", y: a.ys[3], mode: "points", tone: "ink", size: 7, label: false },
      ]}
      vlines={[{ at: d.ytm_pct, label: `YTM ${fmtNum(d.ytm_pct, 2)}`, tone: "ink3", dash: "dot" }]}
      xFormat="num"
      xTitle="YTM %"
      yFormat="num"
      digits={2}
      height={280}
      ariaLabel="Dirty price against yield: exact, duration and duration plus convexity"
    />
  );
}

function CurveStats({ d }: { d: BondOut }) {
  const c = d.curve!;
  return (
    <KV
      rows={[
        { k: "CURVE", v: fmtDate(c.date).toUpperCase() },
        { k: "FAIR · CLEAN", v: fmtNum(c.fair_clean, 3), info: INFO.fair },
        { k: "RICH / CHEAP", v: `${fmtNum(c.rich_cheap, 3, { signed: true })} ${c.rich_cheap > 0 ? "RICH" : c.rich_cheap < 0 ? "CHEAP" : "FAIR"}`, info: INFO.fair },
        { k: "Z-SPREAD", v: `${fmtNum(c.z_spread_bp, 1, { signed: true })} BP`, info: INFO.zspread },
        { k: "D EFF", v: fmtNum(c.effective_duration, 3), info: INFO.effdur },
        { k: "CONV EFF", v: fmtNum(c.effective_convexity, 1), info: INFO.convexity },
        { k: "Σ KRD", v: fmtNum(c.krd_sum, 3), info: INFO.krd },
      ]}
    />
  );
}

function KRD({ d }: { d: BondOut }) {
  const k = d.curve!.key_rate_durations;
  return (
    <>
      <Readline items={[{ k: "Σ KRD", v: fmtNum(d.curve!.krd_sum, 3) }, { k: "D EFF", v: fmtNum(d.curve!.effective_duration, 3) }, { k: "LARGEST $/BP", v: fmtCurrency(Math.max(...k.map((r) => Math.abs(r.krd01_notional))), { digits: 0 }) }]} />
      <BarChart x={k.map((r) => tenorLabel(r.tenor))} y={k.map((r) => r.krd)} yFormat="num" digits={3} height={220} />
    </>
  );
}

type CF = BondOut["cashflows"][number];

function Cashflows({ d }: { d: BondOut }) {
  const cols: Column<CF>[] = [
    { key: "date", label: "Date", render: (r) => <span className="num">{fmtDate(r.date, "short-year").toUpperCase()}</span> },
    { key: "t_years", label: "T · Y", numeric: true, format: (v) => fmtNum(v, 3) },
    { key: "cashflow", label: "CF", numeric: true, format: (v) => fmtNum(v, 4) },
    { key: "pv", label: "PV", numeric: true, format: (v) => fmtNum(v, 4) },
  ];
  const total = d.cashflows.reduce((a, r) => a + r.pv, 0);
  return <DataTable<CF> columns={cols} rows={d.cashflows} rowKey={(r) => r.date} compact maxHeight={22 * 14} footer={<span className="num mc-pad">Σ PV {fmtNum(total, 3)} · DIRTY {fmtNum(d.dirty_price, 3)}</span>} />;
}
