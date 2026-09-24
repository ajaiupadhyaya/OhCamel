/**
 * Backtest tab: walk-forward, out-of-sample comparison of allocation methods against 1/N,
 * with trading costs, and the Sharpe-difference tests explained plainly.
 * POST /portfolio/compare — the heavy endpoint, so it runs on an explicit button.
 */
import { useEffect, useMemo, useState } from "react";
import type { Data } from "plotly.js";
import {
  Chart,
  DataTable,
  NumberField,
  Panel,
  SegmentedControl,
  TimeSeriesChart,
  Toggle,
  type Column,
} from "../../components";
import { Icon } from "../../components/Icon";
import { fmtDate, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import {
  COMPARE_DEFAULT,
  compareBody,
  shallowEqualJson,
  type CompareDraft,
} from "./config";
import { INFO, METHOD_SHORT } from "./info";
import { QPanel, useOpt } from "./shared";
import type { CompareOut, CompareStats, MethodName } from "./types";

const MAX_METHODS = 8; // 8 series colours; 1/N always included
const ALL: MethodName[] = [
  "equal_weight",
  "inverse_volatility",
  "min_variance",
  "max_sharpe",
  "mean_variance",
  "risk_parity",
  "hrp",
  "herc",
  "max_diversification",
  "min_cvar",
];

type Verdict = {
  method: MethodName;
  label: string;
  diff: number | null;
  p: number | null;
  se: number | null;
  better: boolean;
  worse: boolean;
};

export function CompareTab({ enabled }: { enabled: boolean }) {
  const { cfg } = useOpt();
  const [draft, setDraft] = useState<CompareDraft>(COMPARE_DEFAULT);
  const live = useMemo(() => compareBody(cfg, draft), [cfg, draft]);
  const [runBody, setRunBody] = useState<ReturnType<typeof compareBody> | null>(
    null,
  );
  useEffect(() => {
    if (enabled && !runBody && cfg.tickers.length >= 2) setRunBody(live);
  }, [enabled, runBody, live, cfg.tickers.length]);
  const q = useApiPost<CompareOut>("/portfolio/compare", runBody, {
    enabled: enabled && !!runBody,
  });
  const stale = !!runBody && !shallowEqualJson(runBody, live);
  const upd = (p: Partial<CompareDraft>) => setDraft((d) => ({ ...d, ...p }));
  const toggle = (m: MethodName) => {
    if (m === "equal_weight") return;
    const has = draft.methods.includes(m);
    if (!has && draft.methods.length >= MAX_METHODS) return;
    upd({
      methods: has
        ? draft.methods.filter((x) => x !== m)
        : ALL.filter((x) => x === m || draft.methods.includes(x)),
    });
  };
  const running = q.isFetching;

  return (
    <div className="stack-lg">
      <Panel
        title="Walk-forward backtest"
        info={INFO.walkForward}
        subtitle="Every method is re-estimated at each rebalance using only the data it would have had at the time, then held with trading costs until the next one. This is the honest test of an optimiser — and the one most of them fail."
      >
        <div className="op-cmp-controls">
          <div className="oc-field">
            <span className="oc-field-label">
              Methods{" "}
              <span className="subtle">
                ({draft.methods.length}/{MAX_METHODS}; 1/N is the benchmark)
              </span>
            </span>
            <div
              className="op-toggle-chips"
              role="group"
              aria-label="Methods to compare"
            >
              {ALL.map((m) => {
                const on = draft.methods.includes(m);
                const blocked = !on && draft.methods.length >= MAX_METHODS;
                return (
                  <button
                    key={m}
                    type="button"
                    aria-pressed={on}
                    disabled={m === "equal_weight" || blocked}
                    className={`op-tchip ${on ? "on" : ""}`}
                    onClick={() => toggle(m)}
                    title={
                      m === "mean_variance"
                        ? `Uses the rail's mean–variance target (${cfg.mvTarget.replace("_", " ")})`
                        : undefined
                    }
                  >
                    {on && <Icon name="check" size={12} />} {METHOD_SHORT[m]}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="op-cmp-row">
            <div className="oc-field">
              <span className="oc-field-label">Estimation window</span>
              <SegmentedControl
                size="sm"
                ariaLabel="Estimation window"
                options={[
                  { value: "252", label: "1 year" },
                  { value: "504", label: "2 years" },
                  { value: "756", label: "3 years" },
                ]}
                value={String(draft.window)}
                onChange={(v) => upd({ window: Number(v) })}
              />
            </div>
            <div className="oc-field">
              <span className="oc-field-label">Rebalance</span>
              <SegmentedControl
                size="sm"
                ariaLabel="Rebalance frequency"
                options={[
                  { value: "W", label: "Weekly" },
                  { value: "M", label: "Monthly" },
                  { value: "Q", label: "Quarterly" },
                ]}
                value={draft.rebalance}
                onChange={(rebalance) => upd({ rebalance })}
              />
            </div>
            <div className="oc-field">
              <span className="oc-field-label">Expected returns</span>
              <SegmentedControl
                size="sm"
                ariaLabel="Mean estimator"
                options={[
                  { value: "historical", label: "Historical" },
                  { value: "james_stein", label: "James–Stein" },
                ]}
                value={draft.muMethod}
                onChange={(muMethod) => upd({ muMethod })}
              />
            </div>
            <NumberField
              label="Trading cost"
              value={draft.costBps}
              unit="bps"
              min={0}
              max={500}
              step={1}
              width={100}
              onChange={(costBps) => upd({ costBps })}
              info={{
                text: "Proportional cost per unit traded (one way), charged at every rebalance.",
                formula: "\\text{cost}_t = c \\sum_i |\\Delta w_{i,t}|",
              }}
            />
            <span className="spacer" />
            <div className="op-run">
              {stale && <span className="badge warn">settings changed</span>}
              <button
                type="button"
                className="btn btn-primary"
                disabled={running || cfg.tickers.length < 2}
                onClick={() => setRunBody(live)}
              >
                <Icon name={running ? "refresh" : "research"} size={15} />{" "}
                {running ? "Running…" : runBody ? "Run backtest" : "Run"}
              </button>
            </div>
          </div>
          <div className="subtle small">
            Uses the rail's universe, window, covariance estimator (
            {cfg.cov.replace(/_/g, " ")}), risk-free rate and min/max weights.
            The first {draft.window} sessions are used for estimation only.
          </div>
        </div>
      </Panel>

      <QPanel<CompareOut>
        q={q}
        title="Can anything beat 1/N?"
        info={INFO.sharpeTest}
        subtitle="The out-of-sample verdict, with the statistics that back it."
        skeletonHeight={180}
        notes={[]}
        provenance={[]}
        empty={!runBody}
      >
        {(d) => <VerdictBlock d={d} />}
      </QPanel>

      {!q.isError && (
        <>
          <QPanel<CompareOut>
            q={q}
            title="Growth of $1, out of sample"
            info={{
              text: "Value of $1 invested at the first out-of-sample rebalance, after trading costs, for each method.",
              formula: "V_T = \\prod_t (1 + r_{p,t})",
            }}
            subtitle="After trading costs. Log scale makes equal percentage moves look equal."
            skeletonHeight={380}
            notes={[]}
            provenance={[]}
            empty={!runBody}
          >
            {(d) => <EquityChart d={d} />}
          </QPanel>

          <div className="grid-2">
            <QPanel<CompareOut>
              q={q}
              title="Sharpe vs 1/N"
              info={INFO.sharpeTest}
              subtitle="Difference in annual Sharpe ratio with a 95 % interval (Ledoit–Wolf HAC). Intervals crossing zero = no evidence either way."
              skeletonHeight={380}
              notes={[]}
              provenance={[]}
              empty={!runBody}
            >
              {(d) => <ForestPlot d={d} />}
            </QPanel>
            <QPanel<CompareOut>
              q={q}
              title="Drawdowns"
              info="max_drawdown"
              subtitle="How far each method fell from its previous peak. Colours as in the growth chart."
              skeletonHeight={300}
              notes={[]}
              provenance={[]}
              empty={!runBody}
            >
              {(d) => <DrawdownChart d={d} />}
            </QPanel>
          </div>

          <QPanel<CompareOut>
            q={q}
            title="Out-of-sample scorecard"
            subtitle="Realised statistics after costs. Turnover is what you would actually have traded; the cost drag is what it cost."
            flush
            skeletonHeight={300}
            empty={!runBody}
          >
            {(d) => <ScoreTable d={d} />}
          </QPanel>
        </>
      )}
    </div>
  );
}

function verdicts(d: CompareOut): Verdict[] {
  return d.stats
    .filter((s) => d.tests[s.method])
    .map((s) => {
      const t = d.tests[s.method].ledoit_wolf ?? d.tests[s.method].memmel;
      const diff = t?.sharpe_diff_annual ?? null;
      const p = t?.p_value ?? null;
      return {
        method: s.method,
        label: s.label,
        diff,
        p,
        se: t?.se_annual ?? null,
        better: diff != null && p != null && diff > 0 && p < 0.05,
        worse: diff != null && p != null && diff < 0 && p < 0.05,
      };
    });
}

function VerdictBlock({ d }: { d: CompareOut }) {
  const v = verdicts(d);
  const bench = d.stats.find((s) => s.method === d.method.benchmark);
  const better = v.filter((x) => x.better);
  const worse = v.filter((x) => x.worse);
  const beatPoint = v.filter((x) => (x.diff ?? 0) > 0);
  const best = [...d.stats].sort(
    (a, b) => (b.sharpe ?? -Infinity) - (a.sharpe ?? -Infinity),
  )[0];
  const n = v.length;
  const headline =
    better.length === 0
      ? `None of the ${n} optimised method${n === 1 ? "" : "s"} beat 1/N by more than luck would explain.`
      : `${better.length} of ${n} method${n === 1 ? "" : "s"} beat 1/N by a statistically significant margin.`;
  return (
    <div className="op-verdict">
      <p className="op-verdict-head display">{headline}</p>
      <div className="op-verdict-grid">
        <div className="op-verdict-stat">
          <span className="eyebrow">Out-of-sample period</span>
          <span className="num">
            {fmtDate(d.equity.index[0] as string)} –{" "}
            {fmtDate(d.equity.index[d.equity.index.length - 1] as string)}
          </span>
          <span className="subtle small">
            {d.rebalance_dates.length} rebalances · {d.method.window}-day window
            · {fmtNum(d.method.cost_bps, 0)} bps costs
          </span>
        </div>
        <div className="op-verdict-stat">
          <span className="eyebrow">1/N Sharpe</span>
          <span className="num op-big">{fmtNum(bench?.sharpe, 2)}</span>
          <span className="subtle small">
            CAGR {fmtPct(bench?.cagr, 1)} · vol {fmtPct(bench?.annual_vol, 1)}
          </span>
        </div>
        <div className="op-verdict-stat">
          <span className="eyebrow">Highest Sharpe</span>
          <span className="num op-big">{fmtNum(best?.sharpe, 2)}</span>
          <span className="subtle small">{best?.label}</span>
        </div>
        <div className="op-verdict-stat">
          <span className="eyebrow">Tally vs 1/N (5 % level)</span>
          <span className="op-tally">
            <span className="gain num">{better.length}</span> better ·{" "}
            <span className="num">{n - better.length - worse.length}</span>{" "}
            indistinguishable · <span className="loss num">{worse.length}</span>{" "}
            worse
          </span>
          <span className="subtle small">
            {beatPoint.length} of {n} had a higher point estimate
          </span>
        </div>
      </div>
      <div className="op-verdict-text">
        <p>
          {better.length === 0 ? (
            <>
              Some methods may look better on the chart, but over{" "}
              {d.rebalance_dates.length} rebalances the gaps in Sharpe ratio are
              small relative to how noisy Sharpe ratios are.
              {worse.length > 0 && (
                <>
                  {" "}
                  {worse.map((w) => w.label).join(", ")} did significantly{" "}
                  <b>worse</b> than simply splitting the money equally.
                </>
              )}
            </>
          ) : (
            <>
              {better
                .map(
                  (w) =>
                    `${w.label} (+${fmtNum(w.diff, 2)}, p = ${fmtNum(w.p, 3)})`,
                )
                .join("; ")}{" "}
              cleared the bar on this sample. Before trusting it, check it
              survives other windows, rebalance frequencies and costs — with {n}{" "}
              methods tested, one lucky winner is expected now and then.
            </>
          )}
        </p>
        <p className="subtle">
          This is the usual finding. DeMiguel, Garlappi &amp; Uppal (2009)
          tested 14 optimised models on seven datasets and none consistently
          beat the naive 1/N portfolio out of sample: whatever optimisation
          gains in theory, estimation error in the means and covariances gives
          back. By their estimate a sample mean–variance portfolio of 25 assets
          would need around 3,000 months of data to reliably win. Methods that
          skip expected returns (minimum variance, risk parity, HRP) usually
          hold up best.
        </p>
        <p className="subtle small">
          How to read the p-value: it is the probability of seeing a Sharpe gap
          at least this large if the method and 1/N were truly equally good.
          Tests: Ledoit &amp; Wolf (2008) with HAC standard errors (robust to
          fat tails and autocorrelation); Jobson–Korkie with Memmel's (2003)
          correction in the table.
        </p>
      </div>
    </div>
  );
}

function EquityChart({ d }: { d: CompareOut }) {
  const [log, setLog] = useState(true);
  const series = useMemo(
    () =>
      d.equity.columns.map((c) => ({
        name: d.method.labels[c] ?? c,
        x: d.equity.index as string[],
        y: d.equity.data[c],
        width: c === d.method.benchmark ? 2.6 : 1.5,
      })),
    [d],
  );
  return (
    <>
      <div className="op-chart-tools">
        <Toggle label="Log scale" checked={log} onChange={setLog} />
      </div>
      <TimeSeriesChart
        series={series}
        yFormat="num"
        digits={3}
        logY={log}
        baseline={1}
        height={380}
        layout={
          {
            yaxis: { tickformat: "$.2f" },
            legend: { font: { size: 11 } },
          } as never
        }
      />
    </>
  );
}

function DrawdownChart({ d }: { d: CompareOut }) {
  const series = useMemo(
    () =>
      d.drawdown.columns.map((c) => ({
        name: d.method.labels[c] ?? c,
        x: d.drawdown.index as string[],
        y: d.drawdown.data[c],
        width: c === d.method.benchmark ? 2.2 : 1.2,
      })),
    [d],
  );
  return (
    <TimeSeriesChart
      series={series}
      yFormat="pct"
      digits={1}
      height={300}
      baseline={0}
      showLegend={false}
      layout={{ legend: { font: { size: 11 } } } as never}
    />
  );
}

function ForestPlot({ d }: { d: CompareOut }) {
  const v = useMemo(
    () => verdicts(d).sort((a, b) => (b.diff ?? 0) - (a.diff ?? 0)),
    [d],
  );
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const col = (x: Verdict) =>
          x.better ? t.gain : x.worse ? t.loss : t.text2;
        return [
          {
            type: "scatter",
            mode: "markers",
            y: v.map((x) => METHOD_SHORT[x.method]),
            x: v.map((x) => x.diff),
            error_x: {
              type: "data",
              array: v.map((x) => (x.se != null ? 1.96 * x.se : 0)),
              color: t.ruleStrong,
              thickness: 2,
              width: 0,
            },
            marker: {
              size: 10,
              color: v.map(col),
              line: { color: t.surface, width: 1.5 },
            },
            customdata: v.map((x) => [
              x.p,
              x.se != null && x.diff != null ? x.diff - 1.96 * x.se : null,
              x.se != null && x.diff != null ? x.diff + 1.96 * x.se : null,
            ]),
            hovertemplate:
              "<b>%{y}</b><br>ΔSharpe %{x:+.2f} / yr<br>95% CI %{customdata[1]:+.2f} to %{customdata[2]:+.2f}<br>p = %{customdata[0]:.3f}<extra></extra>",
          } as Data,
        ];
      },
    [v],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      showlegend: false,
      margin: { l: 8, r: 12, t: 8, b: 40 },
      xaxis: {
        title: { text: "Sharpe difference vs 1/N (annual)" },
        zeroline: true,
        zerolinecolor: t.text2,
        zerolinewidth: 1.2,
        tickformat: "+.1f",
        showspikes: false,
      },
      yaxis: {
        type: "category",
        autorange: "reversed",
        showgrid: true,
        automargin: true,
      },
      annotations: [
        {
          xref: "x",
          x: 0,
          yref: "paper",
          y: 1,
          yanchor: "bottom",
          xanchor: "right",
          xshift: -4,
          showarrow: false,
          text: "← worse",
          font: { size: 10, color: t.text3 },
        },
        {
          xref: "x",
          x: 0,
          yref: "paper",
          y: 1,
          yanchor: "bottom",
          xanchor: "left",
          xshift: 4,
          showarrow: false,
          text: "better →",
          font: { size: 10, color: t.text3 },
        },
      ],
    }),
    [],
  );
  return (
    <Chart
      data={data}
      layout={layout as never}
      height={Math.max(260, v.length * 40 + 80)}
      ariaLabel="Sharpe difference forest plot"
    />
  );
}

function ScoreTable({ d }: { d: CompareOut }) {
  type Row = CompareStats & {
    diff: number | null;
    p: number | null;
    pm: number | null;
  };
  const rows = useMemo<Row[]>(
    () =>
      d.stats.map((s) => {
        const t = d.tests[s.method];
        return {
          ...s,
          diff: t?.ledoit_wolf?.sharpe_diff_annual ?? null,
          p: t?.ledoit_wolf?.p_value ?? null,
          pm: t?.memmel?.p_value ?? null,
        };
      }),
    [d],
  );
  const cols: Column<Row>[] = [
    {
      key: "label",
      label: "Method",
      render: (r) => (
        <span className={r.method === d.method.benchmark ? "op-strong" : ""}>
          {r.label}
          {r.method === d.method.benchmark && (
            <span className="badge" style={{ marginLeft: 6 }}>
              benchmark
            </span>
          )}
          {(r.failed_rebalances ?? 0) > 0 && (
            <span
              className="badge warn"
              style={{ marginLeft: 6 }}
              title="Rebalances where the optimiser failed and the previous weights were kept"
            >
              {r.failed_rebalances} failed
            </span>
          )}
        </span>
      ),
    },
    {
      key: "cagr",
      label: "CAGR",
      numeric: true,
      format: (v) => fmtSignedPct(v, 1),
      color: "sign",
      info: "cagr",
    },
    {
      key: "annual_vol",
      label: "Vol",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      info: "vol",
    },
    {
      key: "sharpe",
      label: "Sharpe",
      numeric: true,
      format: (v) => fmtNum(v, 2),
      info: "sharpe",
    },
    {
      key: "sortino",
      label: "Sortino",
      numeric: true,
      format: (v) => fmtNum(v, 2),
      info: "sortino",
      hideBelow: 1200,
    },
    {
      key: "max_drawdown",
      label: "Max DD",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      info: "max_drawdown",
      color: () => "loss",
    },
    {
      key: "calmar",
      label: "Calmar",
      numeric: true,
      format: (v) => fmtNum(v, 2),
      info: INFO.calmar,
      hideBelow: 1200,
    },
    {
      key: "annual_turnover",
      label: "Turnover / yr",
      numeric: true,
      format: (v) => fmtPct(v, 0),
      info: INFO.annualTurnover,
      hideBelow: 600,
    },
    {
      key: "cost_drag_annual",
      label: "Cost drag",
      numeric: true,
      format: (v) => fmtPct(v, 2),
      info: INFO.costDrag,
      hideBelow: 900,
    },
    {
      key: "diff",
      label: "ΔSharpe",
      numeric: true,
      format: (v) => (v == null ? "—" : fmtNum(v, 2, { signed: true })),
      color: "sign",
      info: INFO.sharpeTest,
    },
    {
      key: "p",
      label: "p (LW)",
      numeric: true,
      format: (v) => (v == null ? "—" : fmtNum(v, 3)),
      color: (v, r) =>
        v != null && v < 0.05
          ? (r.diff ?? 0) > 0
            ? "gain"
            : "loss"
          : undefined,
      info: {
        title: "p-value, Ledoit–Wolf",
        text: "Probability of a Sharpe gap this large if the method and 1/N were equally good. Below 0.05 is coloured.",
        reference: "Ledoit & Wolf (2008), J. Empirical Finance 15(5)",
      },
    },
    {
      key: "pm",
      label: "p (JK-M)",
      numeric: true,
      format: (v) => (v == null ? "—" : fmtNum(v, 3)),
      hideBelow: 1200,
      info: {
        title: "p-value, Jobson–Korkie–Memmel",
        text: "The classic Sharpe-difference test (assumes normal, independent returns) — a cross-check on the robust one.",
        reference: "Jobson & Korkie (1981); Memmel (2003)",
      },
    },
  ];
  return (
    <DataTable<Row>
      columns={cols}
      rows={rows}
      rowKey={(r) => r.method}
      defaultSort={{ key: "sharpe", dir: "desc" }}
      isActive={(r) => r.method === d.method.benchmark}
    />
  );
}
