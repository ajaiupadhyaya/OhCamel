/**
 * REALIZED (GET /api/options/realized/{ticker}; offline OK on committed OHLC + FRED VIX):
 * five estimators over the rolling window, today's reading by window, the Burghardt–Lane cone
 * (with implied ATM per expiry when a chain is live), and the volatility risk premium.
 * Narrative lives in Methodology (realized-vol); labels here are terse caps.
 */
import { useMemo, type CSSProperties } from "react";
import { DataTable, InfoTip, Panel, Section, SegmentedControl, Select, StatGrid, StatTile, TimeSeriesChart, type Column } from "../../components";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { fmtNum, fmtPct } from "../../lib/format";
import { frameColumn } from "../../lib/series";
import { coneLines, signedParts } from "./derive";
import { ESTIMATOR_LABEL, ESTIMATOR_SHORT, INFO } from "./info";
import { Readline, fmtVolPts } from "./shared";
import { ESTIMATORS, type ConeRow, type Estimator, type Realized } from "./types";
import type { useRealized } from "./shared";

export const WINDOWS = ["10", "21", "63", "126"] as const;
export const YEARS = ["1", "3", "5", "10"] as const;

const CONE_LABEL: Record<number, string> = { 10: "2W", 21: "1M", 42: "2M", 63: "3M", 126: "6M", 252: "1Y" };
const EST_COLOR: Record<Estimator, string> = { yang_zhang: "var(--ink)", close_to_close: "var(--ink-2)", parkinson: "var(--ink-3)", garman_klass: "var(--ink-3)", rogers_satchell: "var(--ink-3)" };

export function RealizedTab({ q, window, setWindow, estimator, setEstimator, years, setYears }: { q: ReturnType<typeof useRealized>; window: string; setWindow: (v: string) => void; estimator: Estimator; setEstimator: (v: Estimator) => void; years: string; setYears: (v: string) => void }) {
  const d = q.data;
  const asOf = d?.as_of;
  return (
    <>
      <Section
        title={
          <>
            Realized · {window}D · ANN
            <Note n={1} to="realized-vol" />
          </>
        }
        actions={
          <>
            <SegmentedControl size="sm" ariaLabel="Rolling window (trading days)" options={WINDOWS.map((w) => ({ value: w, label: `${w}D` }))} value={window} onChange={setWindow} />
            <SegmentedControl size="sm" ariaLabel="History" options={YEARS.map((y) => ({ value: y, label: `${y}Y` }))} value={years} onChange={setYears} />
          </>
        }
      >
        <div className="vx-grid-main">
          <Panel<Realized> title={`Estimators · ${window}D`} query={q} notes={[]} skeletonHeight={360} asOf={asOf}>
            {(r) => <EstimatorChart r={r} />}
          </Panel>
          <Panel<Realized> title="Today · by window" query={q} flush notes={[]} skeletonHeight={360} asOf={asOf}>
            {(r) => <CurrentTable r={r} />}
          </Panel>
        </div>
      </Section>

      <Section
        title={
          <>
            Cone · {ESTIMATOR_SHORT[estimator]} · {years}Y
            <Note n={2} to="realized-vol" />
          </>
        }
        actions={<Select<Estimator> ariaLabel="Cone estimator" value={estimator} onChange={setEstimator} options={ESTIMATORS.map((e) => ({ value: e, label: ESTIMATOR_LABEL[e].toUpperCase() }))} />}
      >
        <div className="vx-grid-main">
          <Panel<Realized> title="Cone" info={INFO.cone} query={q} notes={[]} skeletonHeight={360} asOf={asOf}>
            {(r) => (r.cone.length ? <ConeChart r={r} /> : <Absent reason="HISTORY TOO SHORT FOR A CONE" source={`/api/options/realized/${r.ticker}`} />)}
          </Panel>
          <Panel<Realized> title="Pctile · by horizon" info={INFO.cone_pctile} query={q} notes={["Percentiles use overlapping windows: not independent samples."]} flush skeletonHeight={360} asOf={asOf}>
            {(r) => <ConeTable rows={r.cone} />}
          </Panel>
        </div>
      </Section>

      <Section
        title={
          <>
            VRP · implied − realized
            <Note n={3} to="realized-vol" />
          </>
        }
      >
        <Panel<Realized> title="VRP · now" info={INFO.vrp} query={q} skeletonHeight={110} asOf={d?.vrp?.as_of ?? asOf}>
          {(r) => (r.vrp ? <VrpTiles r={r} /> : <Absent reason="NO IMPLIED VOL FOR THIS UNDERLYING" source="LIVE CHAIN · CBOE IV INDEX (FRED)" />)}
        </Panel>
        {d?.vrp_history && (
          <div className="grid-2">
            <Panel<Realized> title={`Implied · ${d.vrp_history.index} · RV 21D`} query={q} notes={[]} skeletonHeight={300} asOf={asOf}>
              {(r) => <VrpLevels r={r} />}
            </Panel>
            <Panel<Realized> title="VRP · earned · next 21D" info={INFO.vrp_forward} query={q} notes={[]} skeletonHeight={300} asOf={asOf}>
              {(r) => <VrpForward r={r} />}
            </Panel>
          </div>
        )}
      </Section>
    </>
  );
}

// ------------------------------------------------------------------ charts & tables
function EstimatorChart({ r }: { r: Realized }) {
  const series = useMemo(
    () =>
      ESTIMATORS.map((e) => {
        const c = frameColumn(r.rolling, e);
        return { name: ESTIMATOR_SHORT[e], x: c.x, y: c.y, color: EST_COLOR[e], width: e === "yang_zhang" ? 1.75 : 1 };
      }),
    [r],
  );
  return <TimeSeriesChart series={series} yFormat="pct" digits={1} height={340} rangeSelector layout={{ yaxis: { rangemode: "tozero" } } as never} />;
}

type CurRow = { est: Estimator } & Record<string, number | null | Estimator>;

function CurrentTable({ r }: { r: Realized }) {
  const wins = Object.keys(r.current).sort((a, b) => +a - +b);
  const rows: CurRow[] = ESTIMATORS.map((e) => ({ est: e, ...Object.fromEntries(wins.map((w) => [w, r.current[w]?.[e] ?? null])) }) as CurRow);
  const all = rows.flatMap((row) => wins.map((w) => row[w] as number | null)).filter((v): v is number => v != null);
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const columns: Column<CurRow>[] = [
    {
      key: "est",
      label: "Estimator",
      sortable: false,
      render: (row) => (
        <span className="vx-est">
          {ESTIMATOR_SHORT[row.est]} <span className="vx-est-name">{ESTIMATOR_LABEL[row.est].toUpperCase()}</span>
          <InfoTip info={INFO[row.est]} size={12} label={ESTIMATOR_LABEL[row.est]} />
        </span>
      ),
    },
    ...wins.map((w) => ({ key: w, label: `${w}D`, numeric: true, sortable: false, format: (v: number | null) => fmtPct(v, 1), heat: { min: lo, max: hi, diverging: false } }) as Column<CurRow>),
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(row) => row.est} />;
}

function ConeChart({ r }: { r: Realized }) {
  const { x, series, ticks } = useMemo(() => {
    const cone = r.cone;
    const it = r.implied_term;
    // implied ATM per expiry, in trading days (×252/365), within the cone's horizons
    const imp = (it?.dte ?? [])
      .map((dd, i) => ({ x: dd == null ? NaN : (dd * 252) / 365, y: it?.atm_iv[i] ?? null }))
      .filter((p) => Number.isFinite(p.x) && p.y != null && p.x >= 5 && p.x <= 300);
    const xs = [...new Set([...cone.map((c) => c.horizon), ...imp.map((p) => p.x)])].sort((a, b) => a - b);
    const at = (h: number) => cone.findIndex((c) => c.horizon === h);
    const lines = coneLines(cone).map((s): XYSeries => ({ ...s, y: xs.map((v) => (at(v) >= 0 ? s.y[at(v)] : null)), span: true }));
    if (imp.length) lines.push({ name: "IMPLIED", y: xs.map((v) => imp.find((p) => p.x === v)?.y ?? null), mode: "points", tone: "ink2", size: 5 });
    return { x: xs, series: lines, ticks: cone.map((c) => ({ at: c.horizon, label: CONE_LABEL[c.horizon] ?? `${c.horizon}D` })) };
  }, [r]);
  return <XYChart x={x} series={series} logX xTicks={ticks} yFormat="pct" digits={1} zero height={340} xTitle="Horizon · trading days" ariaLabel={`Volatility cone, ${ESTIMATOR_LABEL[r.estimator]}`} />;
}

function ConeTable({ rows }: { rows: ConeRow[] }) {
  const columns: Column<ConeRow>[] = [
    { key: "horizon", label: "Horizon", render: (c) => <span className="num">{c.horizon}D · {CONE_LABEL[c.horizon] ?? ""}</span> },
    { key: "current", label: "Today", numeric: true, format: (v) => fmtPct(v, 1) },
    { key: "p50", label: "Median", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "min", label: "Min", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 1200 },
    { key: "max", label: "Max", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 1200 },
    {
      key: "current_pctile",
      label: "Pctile",
      numeric: true,
      info: INFO.cone_pctile,
      render: (c) => (
        <span className="vx-pctile">
          <span className="vx-pctile-bar" aria-hidden>
            <span style={{ "--at": `${Math.round(c.current_pctile * 100)}%` } as CSSProperties} />
          </span>
          <span className={c.current_pctile >= 0.8 || c.current_pctile <= 0.2 ? "vx-strong" : ""}>{fmtNum(c.current_pctile * 100, 0)}</span>
        </span>
      ),
    },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(c) => c.horizon} />;
}

function VrpTiles({ r }: { r: Realized }) {
  const v = r.vrp!;
  const h = r.vrp_history;
  const src = v.implied_source.split(" (")[0].toUpperCase();
  return (
    <div className="stack">
      <StatGrid min={150}>
        <StatTile label="Implied · 30D" value={v.implied} format={(x) => fmtPct(x, 1)} info={v.implied_source.startsWith("30-day ATM") ? INFO.atm_30d : INFO.model_free} caption={src} />
        <StatTile label={`Realized · ${v.realized_window}D`} value={v.realized} format={(x) => fmtPct(x, 1)} info={v.estimator === "close_to_close" ? INFO.close_to_close : INFO[v.estimator as Estimator]} caption={(ESTIMATOR_LABEL[v.estimator] ?? v.estimator).toUpperCase()} />
        <StatTile label="VRP · pts" value={v.vol_premium} format={(x) => fmtVolPts(x)} tone="auto" info={INFO.vrp} caption="IMPLIED − REALIZED" />
        <StatTile label="Implied / realized" value={v.ratio} format={(x) => `${fmtNum(x, 2)}×`} info={INFO.vrp_ratio} />
        {h && <StatTile label="VRP earned · mean" value={h.mean_premium_forward} format={(x) => fmtVolPts(x)} tone="auto" info={INFO.vrp_forward} caption={`N ${fmtNum(h.frame.index.length, 0)} SESSIONS`} />}
        {h && <StatTile label="VRP earned · > 0" value={h.share_positive_forward} format={(x) => fmtPct(x, 0)} info={INFO.vrp_forward} caption="SHARE OF SESSIONS" />}
      </StatGrid>
      {v.close_to_close && v.estimator !== "close_to_close" && (
        <Readline
          items={[
            { k: "VRP VS CC RV", v: fmtVolPts(v.close_to_close.vol_premium, 2) },
            ...(v.model_free ? [{ k: "MODEL-FREE − CC RV", v: fmtVolPts(v.model_free.vol_premium, 2) }] : []),
          ]}
        />
      )}
    </div>
  );
}

function VrpLevels({ r }: { r: Realized }) {
  const series = useMemo(() => {
    const f = r.vrp_history!.frame;
    const imp = frameColumn(f, "implied");
    const rv = frameColumn(f, "trailing_rv");
    return [
      { name: r.vrp_history!.index, x: imp.x, y: imp.y, color: "var(--ink)", width: 1.25 },
      { name: "RV 21D", x: rv.x, y: rv.y, color: "var(--ink-3)", width: 1 },
    ];
  }, [r]);
  return <TimeSeriesChart series={series} yFormat="pct" digits={1} height={300} rangeSelector />;
}

function VrpForward({ r }: { r: Realized }) {
  const h = r.vrp_history!;
  const series = useMemo(() => {
    const c = frameColumn(h.frame, "premium_forward");
    const { pos, neg } = signedParts(c.y);
    return [
      { name: "EARNED", x: c.x, y: pos, color: "var(--ink)", width: 1, fill: true },
      { name: "LOST", x: c.x, y: neg, color: "var(--signal)", width: 1, fill: true },
    ];
  }, [h]);
  return (
    <>
      <Readline
        items={[
          { k: "MEAN", v: fmtVolPts(h.mean_premium_forward) },
          { k: "> 0", v: fmtPct(h.share_positive_forward, 0) },
          { k: "TRAILING MEAN", v: fmtVolPts(h.mean_premium_trailing) },
        ]}
      />
      <TimeSeriesChart series={series} yFormat="pct" digits={1} height={276} baseline={0} />
    </>
  );
}
