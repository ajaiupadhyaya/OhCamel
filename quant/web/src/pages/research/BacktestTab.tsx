/**
 * BACKTEST (POST /api/backtest/run): the verdict first (one run is never more than ADVISORY;
 * a measured charter gate that fails makes it FAIL), then growth, statistics, drawdowns,
 * rolling Sharpe, calendar and monthly returns, weights, trading, the Sharpe's uncertainty
 * and where the P&L came from. Every number is the run's own; asOf is the window's end.
 */
import { useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { DataTable, InfoTip, Panel, Toggle, type Column } from "../../components";
import { HeatmapChart } from "../../charts/UPlot";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { Note } from "../../design";
import type { ApiError } from "../../lib/api";
import { fmtDate, fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import { backtestVerdict } from "./derive";
import { INFO } from "./info";
import { Readline, VerdictBlock, finite, fmtProb, fmtSR, fmtYears, paramLine } from "./shared";
import type { RunOut, Summary } from "./types";

type Q = UseQueryResult<RunOut, ApiError>;
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];

export function BacktestTab({ q }: { q: Q }) {
  const asOf = q.data?.window.end;
  const cell = { query: q, asOf, notes: [] as string[] };
  return (
    <div className="sl-tab">
      <Panel<RunOut>
        {...cell}
        notes={undefined}
        title={
          <>
            Verdict · single run
            <Note n={3} to="sharpe-inference" />
          </>
        }
        skeletonHeight={300}
      >
        {(r) => <RunVerdict r={r} />}
      </Panel>

      {!q.isError && q.data && (
        <>
          <Panel<RunOut> {...cell} title="Growth of $1 · net · gross · bench" skeletonHeight={400}>
            {(r) => <Equity r={r} />}
          </Panel>

          <div className="grid-2">
            <Panel<RunOut> {...cell} title="Statistics · live window" flush skeletonHeight={560}>
              {(r) => <MetricsTable r={r} />}
            </Panel>
            <div className="sl-main">
              <Panel<RunOut> {...cell} title="Drawdown" info={INFO.drawdown} skeletonHeight={240}>
                {(r) => <XYChart time x={r.drawdown.index as string[]} series={[{ name: "NET", y: r.drawdown.data.net, tone: "signal" }, { name: r.benchmark, y: r.drawdown.data.benchmark, tone: "ink3" }]} yFormat="pct" digits={1} height={240} ariaLabel="Drawdown from the previous peak, strategy net of costs against the benchmark" />}
              </Panel>
              <Panel<RunOut> {...cell} title="Rolling SR · 252D" info={INFO.rolling_sharpe} skeletonHeight={240}>
                {(r) => <XYChart time x={r.rolling.index as string[]} series={[{ name: "NET", y: r.rolling.data.sharpe, tone: "ink" }, { name: r.benchmark, y: r.rolling.data.benchmark_sharpe, tone: "ink3" }]} hlines={[{ at: 0, label: "0", tone: "ink3" }]} digits={2} height={240} ariaLabel="Rolling one-year Sharpe ratio, sampled weekly" />}
              </Panel>
            </div>
          </div>

          <div className="grid-2">
            <Panel<RunOut> {...cell} title="Calendar years · net" flush skeletonHeight={260}>
              {(r) => <CalendarTable r={r} />}
            </Panel>
            <Panel<RunOut> {...cell} title="Monthly returns · net" skeletonHeight={260}>
              {(r) => <MonthlyHeatmap r={r} />}
            </Panel>
          </div>

          <Panel<RunOut> {...cell} title="Weights · month end" info={INFO.weights} skeletonHeight={220}>
            {(r) => <WeightsHeatmap r={r} />}
          </Panel>

          <div className="grid-2">
            <Panel<RunOut> {...cell} title="Turnover · monthly" info={INFO.turnover} skeletonHeight={260}>
              {(r) => <Turnover r={r} />}
            </Panel>
            <Panel<RunOut> {...cell} title="Exposure · weekly" info={INFO.exposure} skeletonHeight={260}>
              {(r) => <Exposure r={r} />}
            </Panel>
          </div>

          <div className="grid-2">
            <Panel<RunOut> {...cell} title="Bootstrap SR · stationary blocks" info={INFO.bootstrap} skeletonHeight={280}>
              {(r) => <Bootstrap r={r} />}
            </Panel>
            <Panel<RunOut> {...cell} title="PSR · min track record" info={INFO.psr} skeletonHeight={280}>
              {(r) => <Psr r={r} />}
            </Panel>
          </div>

          <div className="grid-2">
            <Panel<RunOut> {...cell} title="P&L by asset · gross" flush skeletonHeight={200}>
              {(r) => <PerAssetTable r={r} />}
            </Panel>
            <Panel<RunOut> {...cell} title="Worst drawdowns" info={INFO.max_drawdown} flush skeletonHeight={200}>
              {(r) => <DrawdownTable r={r} />}
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ verdict

function RunVerdict({ r }: { r: RunOut }) {
  const v = backtestVerdict(r);
  const n = r.metrics.net;
  const b = r.metrics.benchmark;
  const e = r.method.engine;
  return (
    <VerdictBlock
      value={v.value}
      detail={v.detail}
      figs={[
        { k: "Live", v: fmtYears(r.window.years), sub: `${fmtDate(r.window.live_start)} – ${fmtDate(r.window.end)} · ${fmtNum(r.window.live_sessions, 0)}D` },
        { k: "SR · net", v: fmtSR(n.sharpe), sub: `${r.benchmark} ${fmtSR(b.sharpe)} · GROSS ${fmtSR(r.metrics.gross.sharpe)}` },
        { k: "CAGR · net", v: fmtPct(n.cagr, 1, { signed: true }), tone: finite(n.cagr) && n.cagr < 0 ? "loss" : "", sub: `${r.benchmark} ${fmtPct(b.cagr, 1, { signed: true })}` },
        { k: "Max DD", v: fmtPct(n.max_drawdown, 1), tone: "loss", sub: `${r.benchmark} ${fmtPct(b.max_drawdown, 1)}` },
        { k: "Turnover", v: fmtMultiple(r.trades.annual_turnover, 1), sub: `COST DRAG ${fmtPct(r.trades.annual_cost_drag, 2)}/Y` },
      ]}
      gates={v.gates}
    >
      <Readline
        items={[
          { k: "PARAMS", v: paramLine(r.params) || "NONE" },
          { k: "EXEC", v: `CLOSE T+${/t\+(\d+)/.exec(e.execution)?.[1] ?? "?"}` },
          { k: "REBAL", v: e.rebalance.toUpperCase() },
          { k: "COST", v: `${fmtNum(e.cost_bps, 0)}BP` },
          { k: "BORROW", v: `${fmtNum(e.borrow_bps_per_year, 0)}BP/Y` },
          e.max_gross_leverage != null && { k: "GROSS ≤", v: `${fmtNum(e.max_gross_leverage, 1)}×` },
          e.vol_target != null && { k: "VOL TGT", v: fmtPct(e.vol_target, 0) },
          { k: "LOOK-AHEAD", v: r.look_ahead_audit.passed ? `PASS · ${r.look_ahead_audit.points} RE-RUNS` : "FAIL", tone: r.look_ahead_audit.passed ? "" : "loss" },
        ]}
      />
    </VerdictBlock>
  );
}

// ------------------------------------------------------------------ charts

function Equity({ r }: { r: RunOut }) {
  const [log, setLog] = useState(true);
  const series = useMemo<XYSeries[]>(
    () => [
      { name: "NET", y: r.equity.data.net, tone: "ink", width: 2 },
      { name: "GROSS", y: r.equity.data.gross, tone: "ink2", dash: "dot" },
      { name: r.benchmark, y: r.equity.data.benchmark, tone: "ink3" },
    ],
    [r],
  );
  return (
    <>
      <div className="sl-chart-tools">
        <Toggle label="LOG" checked={log} onChange={setLog} />
      </div>
      <XYChart time x={r.equity.index as string[]} series={series} yFormat="usd" digits={2} logY={log} hlines={[{ at: 1, label: "$1", tone: "ink3" }]} height={400} ariaLabel="Growth of one dollar: strategy net and gross of costs, and the benchmark held" />
    </>
  );
}

function MonthlyHeatmap({ r }: { r: RunOut }) {
  const rows = [...r.monthly_returns].sort((a, b) => b.year - a.year);
  const keys = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"];
  const z = rows.map((row) => keys.map((k) => (row[k] ?? null) as number | null));
  return <HeatmapChart x={MONTHS} y={rows.map((x) => String(x.year))} z={z} format="pct" digits={1} showValues diverging height={Math.max(220, rows.length * 22 + 40)} colorbar={false} />;
}

function WeightsHeatmap({ r }: { r: RunOut }) {
  const f = r.weights_monthly;
  const x = (f.index as string[]).map((d) => fmtDate(d, "month"));
  return <HeatmapChart x={x} y={f.columns} z={f.columns.map((t) => f.data[t])} format="pct" digits={1} diverging palette="neutral" gap={0} height={Math.max(160, f.columns.length * 22 + 50)} />;
}

function Turnover({ r }: { r: RunOut }) {
  const s = r.turnover_monthly;
  return (
    <>
      <XYChart time x={s.index as string[]} series={[{ name: "TURNOVER", y: s.values, mode: "bars", tone: "ink2" }]} yFormat="x" digits={2} zero height={230} ariaLabel="Notional traded each month as a multiple of capital" />
      <Readline
        items={[
          { k: "EXECUTIONS", v: fmtNum(r.trades.executions, 0) },
          { k: "PER EXEC", v: fmtMultiple(r.trades.avg_turnover_per_execution, 2) },
          { k: "COSTS PAID", v: fmtPct(r.trades.total_cost_paid, 2) },
          { k: "BORROW PAID", v: fmtPct(r.trades.total_borrow_paid, 2) },
        ]}
      />
    </>
  );
}

function Exposure({ r }: { r: RunOut }) {
  const e = r.exposure;
  const hasShort = (e.data.short ?? []).some((v) => finite(v) && v < -1e-6);
  const series = useMemo<XYSeries[]>(() => [{ name: "LONG", y: e.data.long, tone: "ink2" }, ...(hasShort ? [{ name: "SHORT", y: e.data.short, tone: "ink3" as const }] : []), { name: "NET", y: e.data.net, tone: "ink", width: 1.5 }], [e, hasShort]);
  return (
    <>
      <XYChart time x={e.index as string[]} series={series} yFormat="pct" digits={0} zero height={230} ariaLabel="Long, short and net exposure as a share of capital" />
      <Readline
        items={[
          { k: "AVG GROSS", v: fmtMultiple(r.trades.avg_gross_exposure, 2) },
          { k: "AVG NET", v: fmtMultiple(r.trades.avg_net_exposure, 2) },
          { k: "AVG HOLDINGS", v: fmtNum(r.trades.avg_holdings, 1) },
        ]}
      />
    </>
  );
}

function Bootstrap({ r }: { r: RunOut }) {
  const b = r.bootstrap_sharpe;
  const h = b.histogram;
  const data = useMemo(() => {
    if (!h) return null;
    const total = h.counts.reduce((a, c) => a + c, 0) || 1;
    return { x: h.counts.map((_, i) => (h.edges[i] + h.edges[i + 1]) / 2), y: h.counts.map((c) => c / total) };
  }, [h]);
  if (b.error || !data) return <Readline items={[{ k: "BOOTSTRAP", v: (b.error ?? "NO DISTRIBUTION").toUpperCase(), tone: "loss" }]} />;
  return (
    <>
      <XYChart
        x={data.x}
        series={[{ name: "SHARE", y: data.y, mode: "bars", tone: "ink2" }]}
        yFormat="pct"
        digits={1}
        zero
        vlines={[
          { at: 0, label: "0", tone: "signal" },
          ...(finite(b.ci_low) ? [{ at: b.ci_low, label: `2.5% ${fmtSR(b.ci_low)}`, tone: "ink2" as const, dash: "dot" as const }] : []),
          ...(finite(b.ci_high) ? [{ at: b.ci_high, label: `97.5% ${fmtSR(b.ci_high)}`, tone: "ink2" as const, dash: "dot" as const }] : []),
        ]}
        xTitle="SR · annual"
        height={240}
        ariaLabel="Distribution of the annual Sharpe ratio across bootstrap resamples"
      />
      <Readline
        items={[
          { k: "SR", v: fmtSR(b.sharpe) },
          { k: "SE", v: fmtSR(b.std_error) },
          { k: "P(SR≤0)", v: fmtProb(b.prob_sharpe_le_0), tone: finite(b.prob_sharpe_le_0) && b.prob_sharpe_le_0 >= 0.05 ? "loss" : "" },
          { k: "BLOCK", v: `${fmtNum(b.expected_block_length, 1)}D` },
          { k: "RESAMPLES", v: fmtNum(b.reps, 0) },
        ]}
      />
    </>
  );
}

function Psr({ r }: { r: RunOut }) {
  const n = r.metrics.net;
  const psr = r.psr.psr_vs_0;
  const rows: [string, string, string?][] = [
    ["PSR · SR > 0", fmtProb(psr), finite(psr) && psr < 0.7 ? "loss" : ""],
    ["CHARTER BAR", "≥ 70%"],
    ["SR · NET", fmtSR(n.sharpe)],
    ["SESSIONS", fmtNum(n.observations, 0)],
    ["SKEW", fmtNum(n.skew, 2)],
    ["KURTOSIS", fmtNum(n.kurtosis, 1)],
    ["MIN TRL · 95%", fmtYears(n.min_track_record_years_95)],
    ["AVAILABLE", fmtYears(n.years)],
    [`${r.benchmark} PSR`, fmtProb(r.metrics.benchmark.psr_vs_0)],
  ];
  return (
    <dl className="oc-kv num">
      {rows.map(([k, v, tone]) => (
        <div key={k} className="oc-kv-row">
          <dt>{k}</dt>
          <dd className={tone}>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// ------------------------------------------------------------------ tables

type MetricRow = { key: string; label: string; net: number | null; gross: number | null; bench: number | null; fmt: (v: number | null) => string; info?: (typeof INFO)[keyof typeof INFO]; lossTone?: boolean };

function MetricsTable({ r }: { r: RunOut }) {
  const m = r.metrics;
  const row = (key: keyof Summary, label: string, fmt: (v: number | null) => string, info?: MetricRow["info"], lossTone?: boolean): MetricRow => ({ key, label, net: m.net[key] as number | null, gross: m.gross[key] as number | null, bench: m.benchmark[key] as number | null, fmt, info, lossTone });
  const pct1 = (v: number | null) => fmtPct(v, 1);
  const pct2 = (v: number | null) => fmtPct(v, 2);
  const rows: MetricRow[] = [
    row("cagr", "CAGR", (v) => fmtPct(v, 1, { signed: true }), INFO.cagr),
    row("total_return", "Total return", (v) => fmtPct(v, 0, { signed: true }), INFO.total_return),
    row("ann_vol", "Vol", pct1, INFO.vol),
    row("sharpe", "SR", (v) => fmtSR(v), INFO.sharpe),
    row("sortino", "Sortino", (v) => fmtSR(v), INFO.sortino),
    row("calmar", "Calmar", (v) => fmtSR(v), INFO.calmar),
    row("max_drawdown", "Max DD", pct1, INFO.max_drawdown, true),
    row("psr_vs_0", "PSR · SR > 0", (v) => fmtProb(v), INFO.psr),
    row("min_track_record_years_95", "Min TRL · 95%", (v) => fmtYears(v), INFO.min_trl),
    row("hit_rate", "Hit rate", pct1, INFO.hit_rate),
    row("var_95_hist", "VaR 95 · 1D", pct2, INFO.var95),
    row("cvar_95_hist", "CVaR 95 · 1D", pct2, INFO.cvar95),
    row("skew", "Skew", (v) => fmtNum(v, 2), INFO.skew),
    row("kurtosis", "Kurtosis", (v) => fmtNum(v, 1), INFO.kurtosis),
    row("worst_day", "Worst day", pct1, INFO.best_worst, true),
    row("best_day", "Best day", (v) => fmtPct(v, 1, { signed: true }), INFO.best_worst),
  ];
  const rel = r.relative;
  const relRows: MetricRow[] = [
    { key: "alpha", label: "Alpha · ann", net: rel.alpha_ann, gross: null, bench: null, fmt: (v) => fmtPct(v, 1, { signed: true }), info: INFO.alpha },
    { key: "beta", label: "Beta", net: rel.beta, gross: null, bench: null, fmt: (v) => fmtNum(v, 2), info: INFO.beta },
    { key: "corr", label: "Corr", net: rel.correlation, gross: null, bench: null, fmt: (v) => fmtNum(v, 2), info: INFO.correlation },
    { key: "ir", label: "IR", net: rel.information_ratio, gross: null, bench: null, fmt: (v) => fmtSR(v), info: INFO.ir },
    { key: "te", label: "TE", net: rel.tracking_error, gross: null, bench: null, fmt: (v) => fmtPct(v, 1), info: INFO.te },
    { key: "up", label: "Up capture", net: rel.up_capture, gross: null, bench: null, fmt: (v) => fmtPct(v, 0), info: INFO.capture },
    { key: "down", label: "Down capture", net: rel.down_capture, gross: null, bench: null, fmt: (v) => fmtPct(v, 0), info: INFO.capture },
  ];
  const tone = (x: MetricRow, v: number | null) => (x.lossTone || (finite(v) && v < 0 && /CAGR|return|Alpha/i.test(x.label)) ? "loss" : "");
  const cols: Column<MetricRow>[] = [
    { key: "label", label: "Metric", sortable: false, render: (x) => x.label.toUpperCase() },
    { key: "net", label: "Net", numeric: true, sortable: false, render: (x) => <span className={tone(x, x.net)}>{x.fmt(x.net)}</span> },
    { key: "gross", label: "Gross", numeric: true, sortable: false, hideBelow: 500, render: (x) => <span className="sl-gate-dim">{x.gross === null && x.bench === null ? "" : x.fmt(x.gross)}</span> },
    { key: "bench", label: r.benchmark, numeric: true, sortable: false, render: (x) => <span className="sl-gate-dim">{x.gross === null && x.bench === null ? "" : x.fmt(x.bench)}</span> },
  ];
  const withInfo = cols.map((c) => (c.key === "label" ? { ...c, render: (x: MetricRow) => <MetricLabel row={x} /> } : c));
  return (
    <>
      <DataTable<MetricRow> columns={withInfo} rows={rows} rowKey={(x) => x.key} compact />
      <DataTable<MetricRow> columns={withInfo.map((c) => (c.key === "label" ? { ...c, label: `Vs ${r.benchmark}` } : c))} rows={relRows} rowKey={(x) => x.key} compact />
    </>
  );
}

function MetricLabel({ row }: { row: MetricRow }) {
  return (
    <span>
      {row.label.toUpperCase()}
      {row.info && <InfoTip info={row.info} size={12} />}
    </span>
  );
}

function CalendarTable({ r }: { r: RunOut }) {
  type Row = RunOut["calendar_returns"][number] & { diff: number | null };
  const rows: Row[] = r.calendar_returns.map((c) => ({ ...c, diff: finite(c.strategy) && finite(c.benchmark) ? c.strategy - c.benchmark : null }));
  const cols: Column<Row>[] = [
    { key: "year", label: "Year", render: (x) => <span className="num">{x.year}</span> },
    { key: "strategy", label: "Net", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
    { key: "benchmark", label: r.benchmark, numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
    { key: "diff", label: "Δ", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
  ];
  return <DataTable<Row> columns={cols} rows={rows} rowKey={(x) => x.year} defaultSort={{ key: "year", dir: "desc" }} compact />;
}

function PerAssetTable({ r }: { r: RunOut }) {
  const cols: Column<RunOut["per_asset"][number]>[] = [
    { key: "ticker", label: "Asset", render: (x) => <span className="num">{x.ticker}</span> },
    { key: "pnl_contribution", label: "P&L", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign", info: { title: "P&L contribution", text: "Sum over sessions of the weight held into the day times the asset's return, before costs: an arithmetic split of the gross return by asset." } },
    { key: "avg_weight", label: "Avg w", numeric: true, format: (v) => fmtPct(v, 0) },
    { key: "pct_time_long", label: "Long", numeric: true, format: (v) => fmtPct(v, 0) },
    { key: "pct_time_short", label: "Short", numeric: true, format: (v) => fmtPct(v, 0), hideBelow: 500 },
    { key: "traded_notional", label: "Traded", numeric: true, format: (v) => fmtMultiple(v, 0), hideBelow: 600 },
  ];
  return <DataTable columns={cols} rows={r.per_asset} rowKey={(x) => x.ticker} defaultSort={{ key: "pnl_contribution", dir: "desc" }} compact />;
}

function DrawdownTable({ r }: { r: RunOut }) {
  const cols: Column<RunOut["drawdowns"][number]>[] = [
    { key: "depth", label: "Depth", numeric: true, format: (v) => fmtPct(v, 1), color: () => "loss" },
    { key: "peak", label: "Peak", format: (v) => fmtDate(v, "month") },
    { key: "trough", label: "Trough", format: (v) => fmtDate(v, "month") },
    { key: "recovery", label: "Recovered", render: (x) => (x.recovery ? fmtDate(x.recovery, "month") : <span className="loss">OPEN</span>) },
    { key: "sessions_to_recovery", label: "Days", numeric: true, format: (v) => fmtNum(v, 0), hideBelow: 500 },
  ];
  return <DataTable columns={cols} rows={r.drawdowns} rowKey={(x) => x.peak} compact />;
}
