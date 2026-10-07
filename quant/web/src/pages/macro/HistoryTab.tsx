/**
 * HISTORY — GET /api/macro/curve/history?years=&pca_years=: month-end yields at 3M / 2Y / 10Y /
 * 30Y, the slopes (2s10s, 3m10y, 5s30s) with 3m10y inversions shaded and listed, and a PCA of
 * daily yield changes (loadings, variance shares, cumulative factor levels).
 */
import { useMemo, useState } from "react";
import { BarChart, DataTable, Panel, SegmentedControl, StatGrid, StatTile, type Column } from "../../components";
import { XYChart } from "../../charts/XYChart";
import { Note } from "../../design";
import { fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { useApiQuery } from "../../lib/query";
import { inversions, tenorTicks, type Inversion } from "./derive";
import { INFO } from "./info";
import { Controls, Ctl, LiveOnly, Readline, ordinal } from "./shared";
import type { CurveHistory } from "./types";

const YEARS = ["5", "10", "20", "30"] as const;
const PCA_YEARS = ["5", "10", "20"] as const;
const SPREADS: Record<string, { label: string; info: typeof INFO.s2s10 }> = {
  "2s10s": { label: "2S10S", info: INFO.s2s10 },
  "3m10y": { label: "3M10Y", info: INFO.s3m10y },
  "5s30s": { label: "5S30S", info: INFO.s5s30 },
  "2s5s10s": { label: "2S5S10S FLY", info: INFO.fly },
};
const LINE_TENORS: [number, string][] = [
  [0.25, "3M"],
  [2, "2Y"],
  [10, "10Y"],
  [30, "30Y"],
];
const PC_NAME = ["LEVEL", "SLOPE", "CURV"];
const LIVE_ITEMS = [
  { k: "YIELDS · MONTH END · 3M 2Y 10Y 30Y", note: "curve" },
  { k: "SLOPES · 2S10S · 3M10Y · 5S30S · INVERSIONS", note: "recession" },
  { k: "PCA · LOADINGS · VARIANCE SHARE · FACTOR LEVELS", note: "yield-pca" },
];

export function HistoryTab() {
  const [years, setYears] = useState<string>("30");
  const [pcaYears, setPcaYears] = useState<string>("10");
  const q = useApiQuery<CurveHistory>("/macro/curve/history", { years: +years, pca_years: +pcaYears });

  const controls = (
    <Controls>
      <Ctl label="HISTORY">
        <SegmentedControl size="sm" options={YEARS.map((y) => ({ value: y, label: `${y}Y` }))} value={years} onChange={setYears} ariaLabel="History window" />
      </Ctl>
      <Ctl label="PCA">
        <SegmentedControl size="sm" options={PCA_YEARS.map((y) => ({ value: y, label: `${y}Y` }))} value={pcaYears} onChange={setPcaYears} ariaLabel="PCA window" />
      </Ctl>
    </Controls>
  );

  if (q.isError && !q.data)
    return (
      <div className="stack">
        {controls}
        <LiveOnly title="CURVE HISTORY · UST" error={q.error} source="/api/macro/curve/history · FRED" items={LIVE_ITEMS} />
      </div>
    );

  const asOf = q.data?.heatmap.dates[q.data.heatmap.dates.length - 1];
  return (
    <div className="stack">
      {controls}
      <Panel<CurveHistory> query={q} skeletonHeight={96} notes={[]} provenance={[]} asOf={asOf}>
        {(d) => (
          <StatGrid min={140}>
            {Object.entries(d.spreads_latest).map(([k, v]) => (
              <StatTile
                key={k}
                size="sm"
                label={SPREADS[k]?.label ?? k.toUpperCase()}
                info={SPREADS[k]?.info}
                value={`${fmtNum(v.value_bp, 0, { signed: true })} BP`}
                tone={k !== "2s5s10s" && v.value_bp < 0 ? "loss" : "neutral"}
                caption={`${ordinal(v.percentile)} PCTILE · ${fmtDate(v.date, "short").toUpperCase()}`}
              />
            ))}
          </StatGrid>
        )}
      </Panel>
      <Panel<CurveHistory>
        title={
          <>
            YIELDS · MONTH END
            <Note n={1} to="curve" />
          </>
        }
        query={q}
        skeletonHeight={300}
        notes={[]}
        asOf={asOf}
      >
        {(d) => <Yields d={d} />}
      </Panel>
      <div className="grid-3">
        <Panel<CurveHistory>
          title={
            <>
              SLOPES · BP
              <Note n={2} to="recession" />
            </>
          }
          query={q}
          span={2}
          skeletonHeight={300}
          notes={[]}
          provenance={[]}
          asOf={asOf}
        >
          {(d) => <Slopes d={d} />}
        </Panel>
        <Panel<CurveHistory> title="INVERSIONS · 3M10Y" query={q} skeletonHeight={300} notes={[]} provenance={[]} flush asOf={asOf}>
          {(d) => <InversionTable d={d} />}
        </Panel>
      </div>
      <div className="grid-3">
        <Panel<CurveHistory>
          title={
            <>
              PCA · LOADINGS
              <Note n={3} to="yield-pca" />
            </>
          }
          query={q}
          span={2}
          skeletonHeight={280}
          notes={[]}
          provenance={[]}
        >
          {(d) => <Loadings d={d} />}
        </Panel>
        <Panel<CurveHistory> title="PCA · VARIANCE SHARE" query={q} skeletonHeight={280} notes={[]} provenance={[]}>
          {(d) => {
            const ev = d.pca.explained_variance_all.slice(0, 6);
            return (
              <>
                <Readline
                  items={[
                    { k: "N", v: fmtNum(d.pca.n_obs, 0) },
                    { k: "WINDOW", v: `${fmtDate(d.pca.window.start, "month").toUpperCase()} – ${fmtDate(d.pca.window.end, "month").toUpperCase()}` },
                    { k: "PC1–3", v: fmtPct(d.pca.explained_variance.reduce((a, b) => a + b, 0), 1) },
                  ]}
                />
                <BarChart x={ev.map((_, i) => PC_NAME[i] ?? `PC${i + 1}`)} y={ev} yFormat="pct" digits={1} height={220} />
              </>
            );
          }}
        </Panel>
      </div>
      <Panel<CurveHistory> title="PCA · FACTOR LEVELS · WEEKLY · BP" query={q} skeletonHeight={260} asOf={asOf}>
        {(d) => <FactorLevels d={d} />}
      </Panel>
    </div>
  );
}

function Yields({ d }: { d: CurveHistory }) {
  const h = d.heatmap;
  const series = LINE_TENORS.map(([t, label], k) => {
    const j = h.tenors.findIndex((x) => Math.abs(x - t) < 1e-6);
    return { name: label, y: j >= 0 ? h.yields.map((row) => row[j] ?? null) : [], tone: (["ink3", "ink2", "ink", "ink2"] as const)[k], dash: (k === 3 ? "dash" : "solid") as "dash" | "solid", span: true };
  }).filter((s) => s.y.length);
  return <XYChart x={h.dates} time series={series} yFormat="pctPoints" digits={2} height={300} ariaLabel="Month-end Treasury yields at 3 months, 2, 10 and 30 years" />;
}

function inv3m10(d: CurveHistory): Inversion[] {
  return inversions(d.spreads.index, d.spreads.data["3m10y"] ?? []);
}

function Slopes({ d }: { d: CurveHistory }) {
  const s = d.spreads;
  const inv = useMemo(() => inv3m10(d), [d]);
  const series = (["2s10s", "3m10y", "5s30s"] as const)
    .filter((c) => s.columns.includes(c))
    .map((c, i) => ({ name: SPREADS[c].label, y: s.data[c] as (number | null)[], tone: (["ink", "ink2", "ink3"] as const)[i], dash: (i === 2 ? "dot" : "solid") as "dot" | "solid" }));
  return (
    <>
      <Readline items={[{ k: "SHADED", v: "3M10Y < 0" }, { k: "RUNS", v: fmtNum(inv.length, 0) }]} />
      <XYChart x={s.index} time series={series} hlines={[{ at: 0, label: "0", tone: "ink", dash: "solid" }]} bands={inv.map((r) => ({ from: r.start, to: r.end }))} yFormat="num" digits={0} height={280} ariaLabel="Treasury curve slopes in basis points, 3m10y inversions shaded" />
    </>
  );
}

function InversionTable({ d }: { d: CurveHistory }) {
  const rows = inv3m10(d);
  if (!rows.length) return <div className="mc-none num mc-pad">NO INVERSION IN WINDOW</div>;
  const cols: Column<Inversion>[] = [
    { key: "start", label: "From", render: (r) => <span className="num">{fmtDate(r.start, "month").toUpperCase()}</span> },
    { key: "end", label: "To", render: (r) => <span className="num">{r.open ? "OPEN" : fmtDate(r.end, "month").toUpperCase()}</span> },
    { key: "n", label: "Obs", numeric: true, format: (v) => fmtNum(v, 0) },
    { key: "min", label: "Min BP", numeric: true, format: (v) => fmtNum(v, 0, { signed: true }) },
  ];
  return <DataTable<Inversion> columns={cols} rows={rows} rowKey={(r) => r.start} compact maxHeight={22 * 12} />;
}

function Loadings({ d }: { d: CurveHistory }) {
  const L = d.pca.loadings;
  const x = L.index as number[];
  return (
    <XYChart
      x={x}
      logX
      xTicks={tenorTicks(Math.min(...x), Math.max(...x))}
      series={L.columns.slice(0, 3).map((c, i) => ({ name: PC_NAME[i] ?? c.toUpperCase(), y: L.data[c] as (number | null)[], tone: (["ink", "ink2", "ink3"] as const)[i], dash: (i === 2 ? "dash" : "solid") as "dash" | "solid" }))}
      hlines={[{ at: 0, label: "0", tone: "ink3", dash: "dot" }]}
      yFormat="num"
      digits={2}
      height={260}
      ariaLabel="Principal component loadings by maturity"
    />
  );
}

function FactorLevels({ d }: { d: CurveHistory }) {
  const f = d.pca.factor_levels_weekly;
  return (
    <XYChart
      x={f.index}
      time
      series={f.columns.slice(0, 3).map((c, i) => ({ name: PC_NAME[i] ?? c.toUpperCase(), y: f.data[c] as (number | null)[], tone: (["ink", "ink2", "ink3"] as const)[i], dash: (i === 2 ? "dash" : "solid") as "dash" | "solid" }))}
      yFormat="num"
      digits={0}
      height={240}
      ariaLabel="Cumulative principal component factor levels, weekly"
    />
  );
}
