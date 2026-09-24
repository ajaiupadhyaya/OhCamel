/**
 * Backtest tab — POST /api/backtest/run: the verdict, equity (net vs gross vs benchmark),
 * statistics, drawdowns, rolling Sharpe, weights through time, trading activity, and the
 * statistical confidence in the Sharpe ratio (stationary bootstrap + PSR).
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  Chart,
  DataTable,
  HeatmapChart,
  InfoTip,
  Panel,
  SegmentedControl,
  TimeSeriesChart,
  withAlpha,
  type Column,
} from "../../components";
import { Icon } from "../../components/Icon";
import type { ApiError } from "../../lib/api";
import {
  fmtDate,
  fmtMultiple,
  fmtNum,
  fmtPct,
  fmtSignedPct,
  signClass,
} from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import { fmtParams } from "./config";
import { INFO } from "./info";
import {
  Bars,
  BinnedHistogram,
  Verdict,
  finite,
  fmtProb,
  fmtSR,
  fmtYears,
} from "./shared";
import type { RunOut, Summary } from "./types";

type Q = UseQueryResult<RunOut, ApiError>;
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

export function BacktestTab({ q }: { q: Q }) {
  const d = q.data;
  return (
    <div className="sl-tab">
      <Panel<RunOut> query={q} skeletonHeight={260}>
        {(r) => <RunVerdict r={r} />}
      </Panel>

      {q.isError || !q.data ? (
        !q.isError && <EquityPanel q={q} />
      ) : (
        <>
          <EquityPanel q={q} />

          <div className="grid-2">
            <Panel<RunOut>
              title="Statistics"
              subtitle="Net of costs, gross (before costs) and buy-and-hold of the benchmark over the same live window. Green marks where the strategy beats the benchmark."
              info={{
                text: "Every statistic uses daily returns from the first live session to the end of the window. Sharpe, Sortino and PSR use excess returns over T-bills when the risk-free rate is available (see the notes above).",
              }}
              query={q}
              flush
              notes={[]}
              skeletonHeight={620}
            >
              {(r) => <MetricsTable r={r} />}
            </Panel>
            <div className="sl-stack">
              <Panel<RunOut>
                title="Drawdowns"
                info={INFO.drawdown}
                subtitle="How far below its previous high the strategy sat each day, against the benchmark. Shallow, short drawdowns are what make a strategy livable."
                query={q}
                notes={[]}
                skeletonHeight={240}
              >
                {(r) => (
                  <TimeSeriesChart
                    series={[
                      {
                        name: "Strategy (net)",
                        x: r.drawdown.index as string[],
                        y: r.drawdown.data.net,
                        fill: true,
                        color: "var(--loss)",
                        width: 1.4,
                      },
                      {
                        name: `${r.benchmark} buy & hold`,
                        x: r.drawdown.index as string[],
                        y: r.drawdown.data.benchmark,
                        color: "var(--text-3)",
                        width: 1.1,
                      },
                    ]}
                    yFormat="pct"
                    digits={1}
                    height={240}
                    baseline={0}
                  />
                )}
              </Panel>
              <Panel<RunOut>
                title="Rolling one-year Sharpe"
                info={INFO.rolling_sharpe}
                subtitle="Is the edge steady, or did it come from one good stretch? Trailing 252-session window, sampled weekly."
                query={q}
                notes={[]}
                skeletonHeight={240}
              >
                {(r) => (
                  <TimeSeriesChart
                    series={[
                      {
                        name: "Strategy (net)",
                        x: r.rolling.index as string[],
                        y: r.rolling.data.sharpe,
                      },
                      {
                        name: `${r.benchmark} buy & hold`,
                        x: r.rolling.index as string[],
                        y: r.rolling.data.benchmark_sharpe,
                        color: "var(--text-3)",
                        width: 1.1,
                      },
                    ]}
                    yFormat="num"
                    digits={2}
                    height={240}
                    baseline={0}
                  />
                )}
              </Panel>
              <Panel<RunOut>
                title="Year by year"
                subtitle="Calendar-year returns after costs against the benchmark (partial first and last years included)."
                query={q}
                notes={[]}
                skeletonHeight={240}
              >
                {(r) => (
                  <Bars
                    series={[
                      {
                        name: "Strategy (net)",
                        x: r.calendar_returns.map((c) => String(c.year)),
                        y: r.calendar_returns.map((c) => c.strategy),
                      },
                      {
                        name: `${r.benchmark} buy & hold`,
                        x: r.calendar_returns.map((c) => String(c.year)),
                        y: r.calendar_returns.map((c) => c.benchmark),
                        color: "var(--text-3)",
                      },
                    ]}
                    tick=".0%"
                    hover=".1%"
                    height={280}
                  />
                )}
              </Panel>
            </div>
          </div>

          <Panel<RunOut>
            title="What it held"
            info={INFO.weights}
            subtitle="Target weight in each asset at every month end. Reading across a row shows when the rule was long (blue), short (ochre) or out of an asset; reading down a column shows how the book was spread."
            query={q}
            notes={[]}
            skeletonHeight={220}
          >
            {(r) => <WeightsHeatmap r={r} />}
          </Panel>

          <div className="grid-2">
            <Panel<RunOut>
              title="Trading activity"
              info={INFO.turnover}
              subtitle="Notional traded each month as a multiple of capital. Every unit pays the trading-cost assumption — this is where gross turns into net."
              query={q}
              notes={[]}
              skeletonHeight={240}
            >
              {(r) => <TurnoverChart r={r} />}
            </Panel>
            <Panel<RunOut>
              title="Exposure"
              info={INFO.exposure}
              subtitle="Long, short and net exposure through time, sampled weekly. Net above 100% is leverage; below 0 is a net-short book."
              query={q}
              notes={[]}
              skeletonHeight={240}
            >
              {(r) => <ExposureChart r={r} />}
            </Panel>
          </div>

          <div className="grid-2">
            <Panel<RunOut>
              title="How sure can we be of this Sharpe?"
              info={INFO.bootstrap}
              subtitle="The Sharpe ratio re-estimated on 1,000 block-bootstrap resamples of the daily returns. The spread is the uncertainty a single backtest hides."
              query={q}
              notes={[]}
              skeletonHeight={280}
            >
              {(r) => <BootstrapView r={r} />}
            </Panel>
            <Panel<RunOut>
              title="Probabilistic Sharpe Ratio"
              info={INFO.psr}
              subtitle="The probability that the true Sharpe exceeds 0 — adjusting for track-record length, skewness and fat tails."
              query={q}
              notes={[]}
              skeletonHeight={280}
            >
              {(r) => <PsrView r={r} />}
            </Panel>
          </div>

          <Panel<RunOut>
            title="Monthly returns"
            subtitle="Each cell is one month after costs. Runs of red show the regimes the rule struggles in."
            query={q}
            notes={[]}
            skeletonHeight={260}
          >
            {(r) => <MonthlyHeatmap r={r} />}
          </Panel>

          <div className="grid-2">
            <Panel<RunOut>
              title="Where the P&L came from"
              subtitle="Each asset’s contribution to the gross return, its average weight, how often it was held long or short, and how much of it was traded."
              query={q}
              flush
              notes={[]}
              skeletonHeight={200}
            >
              {(r) => <PerAssetTable r={r} />}
            </Panel>
            <Panel<RunOut>
              title="Worst drawdowns"
              info={INFO.max_drawdown}
              subtitle="The five deepest episodes: when they started, bottomed and recovered, and how many sessions each took."
              query={q}
              flush
              notes={[]}
              skeletonHeight={240}
            >
              {(r) => <DrawdownTable r={r} />}
            </Panel>
          </div>
          {d && <EngineStrip r={d} />}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ verdict

function RunVerdict({ r }: { r: RunOut }) {
  const n = r.metrics.net;
  const b = r.metrics.benchmark;
  const ahead = finite(n.cagr) && finite(b.cagr) ? n.cagr >= b.cagr : null;
  const betterSR =
    finite(n.sharpe) && finite(b.sharpe) ? n.sharpe >= b.sharpe : null;
  const psr = r.psr.psr_vs_0;
  const boot = r.bootstrap_sharpe;
  const head = (
    <>
      {fmtPct(n.cagr, 1)} a year after costs,{" "}
      {ahead === null ? (
        ""
      ) : ahead ? (
        <>
          ahead of {r.benchmark}’s {fmtPct(b.cagr, 1)}
        </>
      ) : (
        <>
          behind {r.benchmark}’s {fmtPct(b.cagr, 1)}
        </>
      )}
      {betterSR === null ? (
        "."
      ) : betterSR ? (
        <> — and with a better Sharpe.</>
      ) : (
        <> — and with a lower Sharpe.</>
      )}
    </>
  );
  const sure = finite(psr) ? (psr >= 0.95 ? "clears" : "does not clear") : null;
  return (
    <Verdict
      eyebrow={`${r.strategy.name} · ${fmtDate(r.window.live_start)} – ${fmtDate(r.window.end)} · ${r.window.years.toFixed(1)} years live`}
      head={head}
      tone={ahead ? "gain" : "neutral"}
      stats={[
        {
          label: "Sharpe (net)",
          value: fmtSR(n.sharpe),
          caption: `${r.benchmark} ${fmtSR(b.sharpe)}`,
          info: INFO.sharpe,
        },
        {
          label: "CAGR",
          value: fmtPct(n.cagr, 1),
          caption: `gross ${fmtPct(r.metrics.gross.cagr, 1)}`,
          info: INFO.cagr,
          tone: signClass(n.cagr),
        },
        {
          label: "Max drawdown",
          value: fmtPct(n.max_drawdown, 1),
          caption: `${r.benchmark} ${fmtPct(b.max_drawdown, 1)}`,
          info: INFO.max_drawdown,
          tone: "loss",
        },
        {
          label: "P(true Sharpe > 0)",
          value: fmtProb(psr),
          caption: sure ? `${sure} the 95% bar` : undefined,
          info: INFO.psr,
          tone: finite(psr) ? (psr >= 0.95 ? "gain" : "warn") : "",
        },
        {
          label: "Annual turnover",
          value: fmtMultiple(r.trades.annual_turnover, 1),
          caption: `${fmtPct(r.trades.annual_cost_drag, 2)} a year in costs`,
          info: INFO.turnover,
        },
      ]}
    >
      <p>
        {finite(boot.ci_low) && finite(boot.ci_high) ? (
          <>
            A 95% bootstrap interval for the Sharpe runs from{" "}
            <span className="num">{fmtSR(boot.ci_low)}</span> to{" "}
            <span className="num">{fmtSR(boot.ci_high)}</span>
            {boot.ci_low! > 0
              ? ", entirely above zero."
              : ", which includes zero: this history alone cannot rule out that the true Sharpe is nil."}{" "}
          </>
        ) : null}
        {finite(n.min_track_record_years_95) ||
        n.min_track_record_years_95 === null ? (
          <>
            At this Sharpe, skew and kurtosis you would need about{" "}
            <span className="num">{fmtYears(n.min_track_record_years_95)}</span>{" "}
            of returns to be 95% sure it is positive; the backtest has{" "}
            <span className="num">{r.window.years.toFixed(1)}</span>.
          </>
        ) : null}
      </p>
      <p className="subtle small">
        <span
          className={`badge ${r.look_ahead_audit.passed ? "gain" : "loss"}`}
        >
          <Icon
            name={r.look_ahead_audit.passed ? "check" : "alert"}
            size={11}
          />{" "}
          Look-ahead audit {r.look_ahead_audit.passed ? "passed" : "FAILED"}
        </span>{" "}
        {r.look_ahead_audit.points} truncated re-runs (
        {r.look_ahead_audit.checked.map((c) => fmtDate(c)).join(", ")})
        reproduced the same decisions
        {r.look_ahead_audit.max_abs_diff === 0
          ? " exactly"
          : ` (max weight difference ${fmtNum(r.look_ahead_audit.max_abs_diff, 6)})`}
        . Parameters:{" "}
        <span className="num">{fmtParams(r.params) || "none"}</span>.
      </p>
    </Verdict>
  );
}

function EngineStrip({ r }: { r: RunOut }) {
  const e = r.method.engine;
  return (
    <div className="sl-engine small subtle">
      <span className="eyebrow">Engine</span>
      <span>{e.execution}</span>
      <span>rebalance {e.rebalance}</span>
      <span className="num">{e.cost_bps} bp cost</span>
      <span className="num">{e.borrow_bps_per_year} bp/yr borrow</span>
      {e.max_gross_leverage != null && (
        <span className="num">gross ≤ {e.max_gross_leverage}×</span>
      )}
      {e.vol_target != null && (
        <span className="num">vol target {fmtPct(e.vol_target, 0)}</span>
      )}
      <span>cash {e.cash}</span>
      <span>{r.method.reference}</span>
    </div>
  );
}

// ------------------------------------------------------------------ equity

function EquityPanel({ q }: { q: Q }) {
  const [scale, setScale] = useState<"log" | "linear">("log");
  return (
    <Panel<RunOut>
      title="Growth of $1"
      info={{
        text: "Value of $1 invested at the first live session. Net pays trading costs and borrow fees; gross is the same positions before costs; the benchmark is buy-and-hold with no costs. On a log scale equal percentage moves look equal.",
      }}
      subtitle="Net of costs, gross, and buy-and-hold of the benchmark. The gap between the two strategy lines is what trading cost."
      notes={[]}
      actions={
        <SegmentedControl
          size="sm"
          ariaLabel="Scale"
          options={[
            { value: "log", label: "Log" },
            { value: "linear", label: "Linear" },
          ]}
          value={scale}
          onChange={setScale}
        />
      }
      query={q}
      skeletonHeight={400}
    >
      {(r) => (
        <TimeSeriesChart
          series={[
            {
              name: "Strategy (net)",
              x: r.equity.index as string[],
              y: r.equity.data.net,
              width: 2.2,
            },
            {
              name: "Gross of costs",
              x: r.equity.index as string[],
              y: r.equity.data.gross,
              dash: "dot",
              width: 1.4,
              color: "var(--c1)",
            },
            {
              name: `${r.benchmark} buy & hold`,
              x: r.equity.index as string[],
              y: r.equity.data.benchmark,
              color: "var(--text-3)",
              width: 1.4,
            },
          ]}
          yFormat="usd"
          digits={2}
          logY={scale === "log"}
          baseline={1}
          height={400}
          layout={{ yaxis: { tickformat: ",.2f" } } as any}
        />
      )}
    </Panel>
  );
}

// ------------------------------------------------------------------ metrics table

type MetricRow = {
  key: string;
  label: string;
  net: number | null;
  gross: number | null;
  bench: number | null;
  fmt: (v: number | null) => string;
  info?: any;
  better?: "high" | "low";
};

function MetricsTable({ r }: { r: RunOut }) {
  const m = r.metrics;
  const row = (
    key: keyof Summary,
    label: string,
    fmt: (v: number | null) => string,
    info?: any,
    better?: "high" | "low",
  ): MetricRow => ({
    key,
    label,
    net: m.net[key] as number | null,
    gross: m.gross[key] as number | null,
    bench: m.benchmark[key] as number | null,
    fmt,
    info,
    better,
  });
  const pct1 = (v: number | null) => fmtPct(v, 1);
  const pct2 = (v: number | null) => fmtPct(v, 2);
  const rows: MetricRow[] = [
    row("cagr", "CAGR", pct1, INFO.cagr, "high"),
    row(
      "total_return",
      "Total return",
      (v) => fmtPct(v, 0),
      INFO.total_return,
      "high",
    ),
    row("ann_vol", "Volatility", pct1, INFO.vol),
    row("sharpe", "Sharpe", (v) => fmtSR(v), INFO.sharpe, "high"),
    row("sortino", "Sortino", (v) => fmtSR(v), INFO.sortino, "high"),
    row("calmar", "Calmar", (v) => fmtSR(v), INFO.calmar, "high"),
    row("max_drawdown", "Max drawdown", pct1, INFO.max_drawdown, "high"),
    row("psr_vs_0", "PSR (SR > 0)", (v) => fmtProb(v), INFO.psr, "high"),
    row(
      "min_track_record_years_95",
      "Min. track record",
      (v) => fmtYears(v),
      INFO.min_trl,
      "low",
    ),
    row("hit_rate", "Hit rate", pct1, INFO.hit_rate),
    row("var_95_hist", "VaR 95% (1d)", pct2, INFO.var95, "low"),
    row("cvar_95_hist", "CVaR 95% (1d)", pct2, INFO.cvar95, "low"),
    row("skew", "Skewness", (v) => fmtNum(v, 2), INFO.skew),
    row("kurtosis", "Kurtosis", (v) => fmtNum(v, 1), INFO.kurtosis),
    row("worst_day", "Worst day", pct1, INFO.best_worst),
  ];
  const rel = r.relative;
  const relRows: RelRow[] = [
    {
      key: "alpha",
      label: "Alpha (ann.)",
      value: fmtSignedPct(rel.alpha_ann, 1),
      info: INFO.alpha,
    },
    { key: "beta", label: "Beta", value: fmtNum(rel.beta, 2), info: INFO.beta },
    {
      key: "corr",
      label: "Correlation",
      value: fmtNum(rel.correlation, 2),
      info: INFO.correlation,
    },
    {
      key: "ir",
      label: "Information ratio",
      value: fmtSR(rel.information_ratio),
      info: INFO.ir,
    },
    {
      key: "te",
      label: "Tracking error",
      value: fmtPct(rel.tracking_error, 1),
      info: INFO.te,
    },
    {
      key: "cap",
      label: "Up / down capture",
      value: `${fmtPct(rel.up_capture, 0)} / ${fmtPct(rel.down_capture, 0)}`,
      info: INFO.capture,
    },
  ];
  const beats = (x: MetricRow) => {
    if (!x.better || !finite(x.net) || !finite(x.bench)) return "";
    // (drawdown and VaR are stored as signed losses: higher = better for drawdown, lower = better for VaR/CVaR)
    const good = x.better === "high" ? x.net > x.bench : x.net < x.bench;
    return good ? "sl-beat" : "";
  };
  const cols: Column<MetricRow>[] = [
    {
      key: "label",
      label: "Metric",
      sortable: false,
      render: (x) => <span className="sl-metric-label">{x.label}</span>,
      info: undefined,
    },
    {
      key: "net",
      label: "Net",
      numeric: true,
      sortable: false,
      render: (x) => <span className={beats(x)}>{x.fmt(x.net)}</span>,
    },
    {
      key: "gross",
      label: "Gross",
      numeric: true,
      sortable: false,
      hideBelow: 600,
      render: (x) => <span className="subtle">{x.fmt(x.gross)}</span>,
    },
    {
      key: "bench",
      label: r.benchmark,
      numeric: true,
      sortable: false,
      render: (x) => <span className="subtle">{x.fmt(x.bench)}</span>,
    },
  ];
  const withInfo = cols.map((c) =>
    c.key === "label"
      ? { ...c, render: (x: MetricRow) => <MetricLabel row={x} /> }
      : c,
  );
  return (
    <div className="sl-metrics">
      <DataTable columns={withInfo} rows={rows} rowKey={(x) => x.key} />
      <div className="sl-metrics-sub eyebrow">Relative to {r.benchmark}</div>
      <DataTable
        columns={[
          {
            key: "label",
            label: "Metric",
            sortable: false,
            render: (x: RelRow) => (
              <span className="sl-metric-label">
                {x.label}
                <InfoTip info={x.info} size={12} />
              </span>
            ),
          },
          {
            key: "value",
            label: "Strategy (net)",
            numeric: true,
            sortable: false,
          },
        ]}
        rows={relRows}
        rowKey={(x) => x.key}
      />
    </div>
  );
}

type RelRow = { key: string; label: string; value: string; info: any };

function MetricLabel({ row }: { row: MetricRow }) {
  return (
    <span className="sl-metric-label">
      {row.label}
      {row.info && <InfoTip info={row.info} size={12} />}
    </span>
  );
}

// ------------------------------------------------------------------ weights / trading

function WeightsHeatmap({ r }: { r: RunOut }) {
  const f = r.weights_monthly;
  const tickers = f.columns;
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const z = tickers.map((tk) => f.data[tk]);
        const flat = z.flat().filter(finite);
        const m = Math.max(0.05, ...flat.map(Math.abs));
        const ramp = t.divergingNeutral;
        return [
          {
            type: "heatmap",
            x: f.index,
            y: tickers,
            z,
            zmin: -m,
            zmax: m,
            zmid: 0,
            colorscale: ramp.map((c, i) => [i / (ramp.length - 1), c]),
            xgap: 0,
            ygap: 3,
            colorbar: {
              thickness: 8,
              outlinewidth: 0,
              tickformat: ".0%",
              tickfont: { family: t.fontMono, size: 10, color: t.text3 },
              len: 0.9,
            },
            hovertemplate:
              "%{y} · %{x|%b %Y}<br><b>%{z:.1%}</b><extra></extra>",
            hoverongaps: false,
          } as unknown as Data,
        ];
      },
    [f, tickers],
  );
  return (
    <Chart
      data={data}
      layout={
        {
          xaxis: {
            type: "date",
            showgrid: false,
            showspikes: false,
            showline: false,
          },
          yaxis: { type: "category", autorange: "reversed", showgrid: false },
          margin: { l: 8, r: 8, t: 8, b: 28 },
        } as any
      }
      height={Math.max(160, tickers.length * 30 + 50)}
    />
  );
}

function TurnoverChart({ r }: { r: RunOut }) {
  const s = r.turnover_monthly;
  return (
    <>
      <Bars
        series={[{ name: "Turnover", x: s.index as string[], y: s.values }]}
        dateX
        tick=".1f"
        hover=".2f"
        suffix="×"
        height={250}
        layout={{ bargap: 0.1 }}
      />
      <div className="sl-mini-stats small">
        <span>
          <span className="subtle">Executions</span>{" "}
          <span className="num">{r.trades.executions}</span>
        </span>
        <span>
          <span className="subtle">Avg. per execution</span>{" "}
          <span className="num">
            {fmtMultiple(r.trades.avg_turnover_per_execution, 2)}
          </span>
        </span>
        <span>
          <span className="subtle">Costs paid</span>{" "}
          <span className="num">{fmtPct(r.trades.total_cost_paid, 2)}</span>
        </span>
        <span>
          <span className="subtle">Borrow paid</span>{" "}
          <span className="num">{fmtPct(r.trades.total_borrow_paid, 2)}</span>
        </span>
      </div>
    </>
  );
}

function ExposureChart({ r }: { r: RunOut }) {
  const e = r.exposure;
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const x = e.index as string[];
        const long = t.divergingNeutral[5];
        const short = t.divergingNeutral[1];
        const hasShort = (e.data.short ?? []).some(
          (v) => finite(v) && v < -1e-6,
        );
        return [
          {
            type: "scatter",
            mode: "lines",
            name: "Long",
            x,
            y: e.data.long,
            line: { color: long, width: 1, shape: "hv" },
            fill: "tozeroy",
            fillcolor: withAlpha(long, 0.28),
            hovertemplate: "<b>Long</b> %{y:.0%}<extra></extra>",
          },
          ...(hasShort
            ? [
                {
                  type: "scatter",
                  mode: "lines",
                  name: "Short",
                  x,
                  y: e.data.short,
                  line: { color: short, width: 1, shape: "hv" },
                  fill: "tozeroy",
                  fillcolor: withAlpha(short, 0.32),
                  hovertemplate: "<b>Short</b> %{y:.0%}<extra></extra>",
                },
              ]
            : []),
          {
            type: "scatter",
            mode: "lines",
            name: "Net",
            x,
            y: e.data.net,
            line: { color: t.text, width: 1.6, shape: "hv" },
            hovertemplate: "<b>Net</b> %{y:.0%}<extra></extra>",
          },
        ] as Data[];
      },
    [e],
  );
  return (
    <>
      <Chart
        data={data}
        layout={
          {
            hovermode: "x unified",
            showlegend: true,
            xaxis: { type: "date", hoverformat: "%d %b %Y" },
            yaxis: { tickformat: ".0%", side: "right", zeroline: true },
            margin: { l: 8, r: 8, t: 30, b: 28 },
          } as any
        }
        height={250}
      />
      <div className="sl-mini-stats small">
        <span>
          <span className="subtle">Avg. gross</span>{" "}
          <span className="num">
            {fmtMultiple(r.trades.avg_gross_exposure, 2)}
          </span>
        </span>
        <span>
          <span className="subtle">Avg. net</span>{" "}
          <span className="num">
            {fmtMultiple(r.trades.avg_net_exposure, 2)}
          </span>
        </span>
        <span>
          <span className="subtle">Avg. holdings</span>{" "}
          <span className="num">{fmtNum(r.trades.avg_holdings, 1)}</span>
        </span>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ confidence

function BootstrapView({ r }: { r: RunOut }) {
  const b = r.bootstrap_sharpe;
  if (b.error || !b.histogram)
    return (
      <p className="subtle small">
        Bootstrap not available: {b.error ?? "no distribution returned"}.
      </p>
    );
  return (
    <>
      <BinnedHistogram
        counts={b.histogram.counts}
        edges={b.histogram.edges}
        split={0}
        xTitle="Annualized Sharpe ratio across resamples"
        height={250}
        vlines={[
          {
            x: b.ci_low!,
            label: `2.5%: ${fmtSR(b.ci_low)}`,
            color: "var(--text-2)",
          },
          {
            x: b.ci_high!,
            label: `97.5%: ${fmtSR(b.ci_high)}`,
            color: "var(--text-2)",
          },
        ]}
      />
      <div className="sl-mini-stats small">
        <span>
          <span className="subtle">Point estimate</span>{" "}
          <span className="num">{fmtSR(b.sharpe)}</span>
        </span>
        <span>
          <span className="subtle">Std. error</span>{" "}
          <span className="num">{fmtSR(b.std_error)}</span>
        </span>
        <span>
          <span className="subtle">Resamples ≤ 0</span>{" "}
          <span className="num">{fmtProb(b.prob_sharpe_le_0)}</span>
        </span>
        <span>
          <span className="subtle">Mean block</span>{" "}
          <span className="num">{fmtNum(b.expected_block_length, 1)} days</span>
        </span>
      </div>
      <p className="subtle small sl-method">
        {b.method} · {b.reps?.toLocaleString()} resamples. Red bars are
        resamples with a negative Sharpe.
      </p>
    </>
  );
}

function PsrView({ r }: { r: RunOut }) {
  const n = r.metrics.net;
  const psr = r.psr.psr_vs_0;
  const pct = finite(psr) ? psr : 0;
  const trl = n.min_track_record_years_95;
  return (
    <div className="sl-psr">
      <div className="sl-psr-big">
        <span
          className={`num display ${finite(psr) ? (psr >= 0.95 ? "gain" : "warn") : ""}`}
        >
          {fmtProb(psr)}
        </span>
        <span className="subtle">probability the true Sharpe exceeds 0</span>
      </div>
      <PsrGauge chance={pct} />
      <p className="small">
        A Sharpe of <span className="num">{fmtSR(n.sharpe)}</span> over{" "}
        <span className="num">{n.observations.toLocaleString()}</span> sessions,
        with skewness <span className="num">{fmtNum(n.skew, 2)}</span> and
        kurtosis <span className="num">{fmtNum(n.kurtosis, 1)}</span>.{" "}
        {finite(psr) && psr >= 0.95
          ? "That clears the conventional 95% bar: the positive Sharpe is unlikely to be an accident of this sample."
          : "That falls short of the conventional 95% bar: a strategy with no true edge could plausibly have produced this record."}{" "}
        Negative skew and fat tails widen the uncertainty, which is why PSR is
        more honest than the Sharpe ratio alone.
      </p>
      <div className="sl-mini-stats small">
        <span>
          <span className="subtle">Min. track record (95%)</span>{" "}
          <span className="num">{fmtYears(trl)}</span>
        </span>
        <span>
          <span className="subtle">Available</span>{" "}
          <span className="num">{fmtYears(n.years)}</span>
        </span>
        <span>
          <span className="subtle">{r.benchmark} PSR</span>{" "}
          <span className="num">{fmtProb(r.metrics.benchmark.psr_vs_0)}</span>
        </span>
      </div>
      <p className="subtle small sl-method">{r.psr.reference}.</p>
    </div>
  );
}

function PsrGauge({ chance }: { chance: number }) {
  return (
    <div
      className="sl-gauge"
      role="img"
      aria-label={`PSR ${(chance * 100).toFixed(0)}%`}
    >
      <div className="sl-gauge-track">
        <div
          className="sl-gauge-fill"
          style={{ width: `${Math.max(0, Math.min(1, chance)) * 100}%` }}
        />
        <div className="sl-gauge-mark" style={{ left: "95%" }} />
      </div>
      <div className="sl-gauge-scale num">
        <span style={{ left: 0 }}>0%</span>
        <span style={{ left: "50%" }}>50%</span>
        <span style={{ left: "95%" }}>95%</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ tables

function MonthlyHeatmap({ r }: { r: RunOut }) {
  const rows = [...r.monthly_returns].sort((a, b) => b.year - a.year);
  const keys = [
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
  ];
  const z = rows.map((row) =>
    keys.map((k) => (row[k] ?? null) as number | null),
  );
  return (
    <HeatmapChart
      x={MONTHS}
      y={rows.map((x) => String(x.year))}
      z={z}
      format="pct"
      digits={1}
      showValues
      diverging
      height={Math.max(220, rows.length * 26 + 30)}
      colorbar={false}
    />
  );
}

function PerAssetTable({ r }: { r: RunOut }) {
  const cols: Column<RunOut["per_asset"][number]>[] = [
    {
      key: "ticker",
      label: "Asset",
      render: (x) => (
        <span className="num">
          <span
            className="oc-chip-dot sl-dot"
            style={{
              background: `var(--c${(r.tickers.indexOf(x.ticker) % 8) + 1})`,
            }}
          />
          {x.ticker}
        </span>
      ),
    },
    {
      key: "pnl_contribution",
      label: "P&L",
      numeric: true,
      format: (v) => fmtSignedPct(v, 1),
      color: "sign",
      info: {
        text: "Sum over sessions of the weight held going into the day × the asset's return, before costs: an arithmetic (not compounded) split of the gross return by asset.",
      },
    },
    {
      key: "avg_weight",
      label: "Avg. wt",
      numeric: true,
      format: (v) => fmtPct(v, 0),
    },
    {
      key: "pct_time_long",
      label: "Long / short",
      numeric: true,
      render: (x) => (
        <span>
          {fmtPct(x.pct_time_long, 0)}{" "}
          <span className="subtle">/ {fmtPct(x.pct_time_short, 0)}</span>
        </span>
      ),
      info: {
        text: "Share of live sessions the asset was held long, and short.",
      },
    },
    {
      key: "traded_notional",
      label: "Traded",
      numeric: true,
      format: (v) => fmtMultiple(v, 0),
      hideBelow: 600,
      info: {
        text: "Total notional traded in this asset over the backtest, as a multiple of capital.",
      },
    },
  ];
  return (
    <DataTable
      columns={cols}
      rows={r.per_asset}
      rowKey={(x) => x.ticker}
      defaultSort={{ key: "pnl_contribution", dir: "desc" }}
    />
  );
}

function DrawdownTable({ r }: { r: RunOut }) {
  const cols: Column<RunOut["drawdowns"][number]>[] = [
    {
      key: "depth",
      label: "Depth",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      color: "sign",
    },
    { key: "peak", label: "Peak", format: (v) => fmtDate(v, "month") },
    { key: "trough", label: "Trough", format: (v) => fmtDate(v, "month") },
    {
      key: "recovery",
      label: "Recovered",
      render: (x) =>
        x.recovery ? (
          fmtDate(x.recovery, "month")
        ) : (
          <span className="badge warn">not yet</span>
        ),
    },
    {
      key: "sessions_to_recovery",
      label: "Sessions",
      numeric: true,
      render: (x) => (
        <span title={`${x.sessions_to_trough} sessions peak to trough`}>
          {x.sessions_to_recovery ?? "—"}
        </span>
      ),
      info: {
        text: "Trading sessions from the peak until the previous high was regained (hover a value for peak-to-trough).",
      },
    },
  ];
  return <DataTable columns={cols} rows={r.drawdowns} rowKey={(x) => x.peak} />;
}
