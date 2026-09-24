/**
 * Allocation tab: the optimiser's weights, their ex-ante statistics, where the risk comes
 * from, the HRP/HERC tree, and what the model expects each asset to return.
 */
import { useMemo } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type { Data } from "plotly.js";
import { Link } from "react-router-dom";
import {
  Chart,
  DataTable,
  Panel,
  StatGrid,
  StatTile,
  type Column,
} from "../../components";
import { Icon } from "../../components/Icon";
import type { ApiError } from "../../lib/api";
import { fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import { currentInUniverse } from "./config";
import { Dendrogram } from "./Dendrogram";
import { INFO, METHOD_SHORT, RETURNS_LABEL } from "./info";
import { methodInfo } from "./MethodPicker";
import { QPanel, sortBy, useOpt } from "./shared";
import type { OptimizeOut } from "./types";

export function AllocationTab({
  q,
  onApply,
  applied,
}: {
  q: UseQueryResult<OptimizeOut, ApiError>;
  onApply: (d: OptimizeOut) => void;
  applied: boolean;
}) {
  const { cfg, spec } = useOpt();
  const cur = currentInUniverse(cfg);
  const d = q.data;
  const isTree = d?.result.method === "hrp" || d?.result.method === "herc";

  return (
    <div className="stack-lg">
      <QPanel<OptimizeOut>
        q={q}
        title={spec ? spec.label : "Result"}
        info={methodInfo(spec)}
        subtitle="Ex-ante figures: what the estimates say these weights should deliver. They come from the same data used to choose the weights, so they flatter the optimiser — the Backtest tab has the out-of-sample evidence."
        skeletonHeight={120}
        actions={
          d && (
            <>
              <button
                type="button"
                className={`btn ${applied ? "" : "btn-primary"}`}
                onClick={() => onApply(d)}
                disabled={applied}
                title="Replace the holdings of your active portfolio with these weights"
              >
                <Icon name={applied ? "check" : "portfolio"} size={15} />{" "}
                {applied ? "Applied to portfolio" : "Apply to my portfolio"}
              </button>
              {applied && (
                <Link to="/portfolio" className="btn btn-ghost">
                  Open Portfolio Lab <Icon name="arrow-right" size={14} />
                </Link>
              )}
            </>
          )
        }
      >
        {(d) => <Headline d={d} current={cur} />}
      </QPanel>

      {!q.isError && (
        <>
          <div className="grid-2">
            <QPanel<OptimizeOut>
              q={q}
              title="Weights"
              info={{
                text: "How the budget is split across assets. Hover a bar for the exact value; the lighter bars are your current portfolio (rescaled to the universe), so the gap is the trade.",
              }}
              subtitle={
                cur
                  ? "Optimal vs your current portfolio"
                  : "Optimal weights, largest first"
              }
              skeletonHeight={300}
              notes={[]}
              provenance={[]}
            >
              {(d) => <WeightsChart d={d} current={cur} />}
            </QPanel>
            <QPanel<OptimizeOut>
              q={q}
              title="Risk contributions"
              info={INFO.riskContribution}
              subtitle={
                d?.result.method === "risk_parity"
                  ? "Equal risk contribution: every bar should sit on the dotted 1/N line"
                  : "Where the volatility actually comes from — compare with the weights"
              }
              skeletonHeight={300}
              notes={[]}
              provenance={[]}
            >
              {(d) => <RiskChart d={d} />}
            </QPanel>
          </div>

          <QPanel<OptimizeOut>
            q={q}
            title="Allocation detail"
            subtitle="Every asset: target weight, the trade from current, its share of risk and the inputs the optimiser saw."
            flush
            skeletonHeight={260}
            provenance={[]}
            notes={[]}
          >
            {(d) => <AllocationTable d={d} current={cur} />}
          </QPanel>

          {isTree && d?.result.extra.dendrogram && (
            <div className={d.result.extra.bisection ? "grid-3" : ""}>
              <Panel
                span={d.result.extra.bisection ? 2 : undefined}
                title={
                  d.result.method === "hrp" ? "The HRP tree" : "The HERC tree"
                }
                info={INFO.corrDistance}
                subtitle={`Assets that move together are joined low in the tree. Linkage: ${String(d.result.params.linkage ?? "")}. Leaf labels show each asset's weight; hover a node for the cluster's total.`}
              >
                <Dendrogram
                  d={d.result.extra.dendrogram}
                  weights={d.result.weights}
                  height={320}
                />
              </Panel>
              {d.result.extra.bisection && (
                <Panel
                  title="How the money was split"
                  info={INFO.bisection}
                  subtitle="Top-down: each split gives the lower-variance half more money."
                >
                  <Bisection steps={d.result.extra.bisection} />
                </Panel>
              )}
            </div>
          )}

          <QPanel<OptimizeOut>
            q={q}
            title="What the model expects"
            info={INFO.standardError}
            subtitle={
              d?.expected_returns.model === "historical"
                ? "Historical mean return per asset with a ±2 standard-error band. The bands are wide: ten years of data barely pins down an average — which is why methods that ignore expected returns often do better out of sample."
                : `Expected returns from the ${RETURNS_LABEL[d?.expected_returns.model ?? "historical"]} model next to the raw historical mean.`
            }
            skeletonHeight={280}
            notes={[]}
            provenance={[]}
          >
            {(d) => <ExpectedChart d={d} />}
          </QPanel>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ headline
function Headline({
  d,
  current,
}: {
  d: OptimizeOut;
  current: Record<string, number> | null;
}) {
  const r = d.result;
  const n = d.universe.tickers.length;
  const turnover = current
    ? d.universe.tickers.reduce(
        (a, t) => a + Math.abs((r.weights[t] ?? 0) - (current[t] ?? 0)),
        0,
      )
    : null;
  const rfNote =
    d.risk_free.source === "user"
      ? `r_f ${fmtPct(d.risk_free.annual, 2)} (yours)`
      : d.risk_free.source === "market"
        ? `r_f ${fmtPct(d.risk_free.annual, 2)} (3M bill)`
        : undefined;
  return (
    <>
      <StatGrid min={128}>
        <StatTile
          label="Exp. return"
          value={r.expected_return}
          format={(v) => fmtPct(v, 1)}
          info={INFO.exAnteReturn}
          caption="per year, ex-ante"
        />
        <StatTile
          label="Volatility"
          value={r.volatility}
          format={(v) => fmtPct(v, 1)}
          info={INFO.exAnteVol}
          caption="per year, ex-ante"
        />
        <StatTile
          label="Sharpe"
          value={r.sharpe}
          format={(v) => fmtNum(v, 2)}
          info={INFO.exAnteSharpe}
          caption={r.sharpe == null ? "needs a risk-free rate" : rfNote}
        />
        <StatTile
          label="Eff. bets"
          value={r.effective_bets}
          format={(v) => fmtNum(v, 2)}
          info={INFO.enb}
          caption={`of ${n} assets`}
        />
        <StatTile
          label="Div. ratio"
          value={r.diversification_ratio}
          format={(v) => `${fmtNum(v, 2)}×`}
          info={INFO.diversificationRatio}
        />
        <StatTile
          label="Eff. N"
          value={r.effective_n}
          format={(v) => fmtNum(v, 1)}
          info={INFO.effectiveN}
          caption="equal-sized positions"
        />
        {r.extra.cvar_daily != null && (
          <StatTile
            label={`Daily CVaR ${fmtPct(Number(r.params.cvar_alpha ?? 0.95), 0)}`}
            value={r.extra.cvar_daily}
            format={(v) => fmtPct(v, 2)}
            tone="loss"
            info={INFO.cvar}
            caption={
              r.extra.var_daily != null
                ? `VaR ${fmtPct(r.extra.var_daily, 2)} · ${r.extra.scenarios ?? ""} days`
                : undefined
            }
          />
        )}
        {turnover != null && (
          <StatTile
            label="Turnover"
            value={turnover}
            format={(v) => fmtPct(v, 0)}
            info={{
              ...INFO.turnover,
              text: `${INFO.turnover.text} Computed in your browser from the two sets of weights.`,
            }}
            caption="one-way, from current"
          />
        )}
      </StatGrid>
      <div className="op-meta small subtle">
        <span>
          <span className="num">
            {d.universe.observations.toLocaleString()}
          </span>{" "}
          daily returns · {d.universe.start} → {d.universe.end}
        </span>
        <span>
          Σ: {d.covariance.estimator.replace(/_/g, " ")}
          {d.covariance.shrinkage != null && (
            <>
              {" "}
              · shrinkage{" "}
              <span className="num">{fmtNum(d.covariance.shrinkage, 3)}</span>
            </>
          )}
          {d.covariance.condition_number != null && (
            <>
              {" "}
              · κ{" "}
              <span className="num">
                {fmtNum(d.covariance.condition_number, 0)}
              </span>
            </>
          )}
        </span>
        <span>μ: {RETURNS_LABEL[d.expected_returns.model]}</span>
        {r.reference && <span className="op-meta-ref">{r.reference}</span>}
      </div>
      {r.notes.length > 0 && (
        <div className="op-inline-notes small">
          {r.notes.map((n, i) => (
            <div key={i} className="op-warn">
              {n}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------ charts
function WeightsChart({
  d,
  current,
}: {
  d: OptimizeOut;
  current: Record<string, number> | null;
}) {
  const order = useMemo(
    () => sortBy(d.universe.tickers, d.result.weights),
    [d],
  );
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const out: Data[] = [
          {
            type: "bar",
            orientation: "h",
            name: METHOD_SHORT[d.result.method],
            y: order,
            x: order.map((k) => d.result.weights[k]),
            marker: { color: t.categorical[0] },
            hovertemplate: `<b>%{y}</b>  %{x:.2%}<extra>${METHOD_SHORT[d.result.method]}</extra>`,
          } as Data,
        ];
        if (current)
          out.push({
            type: "bar",
            orientation: "h",
            name: "Current",
            y: order,
            x: order.map((k) => current[k] ?? 0),
            marker: { color: t.text3, opacity: 0.45 },
            hovertemplate: "<b>%{y}</b>  %{x:.2%}<extra>current</extra>",
          } as Data);
        return out;
      },
    [d, order, current],
  );
  const layout = useMemo(
    () => ({
      barmode: "group",
      bargap: 0.28,
      bargroupgap: 0.08,
      showlegend: !!current,
      margin: { l: 8, r: 12, t: current ? 28 : 8, b: 28 },
      xaxis: {
        tickformat: ".0%",
        zeroline: true,
        showgrid: true,
        showspikes: false,
      },
      yaxis: {
        type: "category",
        autorange: "reversed",
        showgrid: false,
        automargin: true,
        tickfont: { size: 11 },
      },
    }),
    [current],
  );
  return (
    <Chart
      data={data}
      layout={layout as never}
      height={Math.max(240, order.length * (current ? 30 : 24) + 60)}
      ariaLabel="Portfolio weights"
    />
  );
}

function RiskChart({ d }: { d: OptimizeOut }) {
  const order = useMemo(
    () => sortBy(d.universe.tickers, d.result.weights),
    [d],
  );
  const n = order.length;
  const erc = d.result.method === "risk_parity";
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => [
        {
          type: "bar",
          orientation: "h",
          name: "Risk share",
          y: order,
          x: order.map((k) => d.result.risk_contributions_pct[k]),
          customdata: order.map((k) => [
            d.result.weights[k],
            d.result.risk_contributions[k],
          ]),
          marker: {
            color: order.map((k) =>
              (d.result.risk_contributions_pct[k] ?? 0) < 0
                ? t.gain
                : t.categorical[1],
            ),
          },
          hovertemplate:
            "<b>%{y}</b>  %{x:.1%} of risk<br>weight %{customdata[0]:.1%} · adds %{customdata[1]:.2%} vol<extra></extra>",
        } as Data,
      ],
    [d, order],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      showlegend: false,
      margin: { l: 8, r: 12, t: 22, b: 28 },
      xaxis: {
        tickformat: ".0%",
        zeroline: true,
        showgrid: true,
        showspikes: false,
      },
      yaxis: {
        type: "category",
        autorange: "reversed",
        showgrid: false,
        automargin: true,
        tickfont: { size: 11 },
      },
      shapes: [
        {
          type: "line",
          yref: "paper",
          y0: 0,
          y1: 1,
          x0: 1 / n,
          x1: 1 / n,
          line: {
            color: erc ? t.text : t.ruleStrong,
            width: erc ? 1.5 : 1,
            dash: "dot",
          },
        },
      ],
      annotations: [
        {
          yref: "paper",
          y: 1,
          x: 1 / n,
          xanchor: "left",
          yanchor: "bottom",
          xshift: 4,
          showarrow: false,
          text: `equal risk 1/${n}`,
          font: { size: 10, color: erc ? t.text2 : t.text3 },
        },
      ],
    }),
    [n, erc],
  );
  return (
    <Chart
      data={data}
      layout={layout as never}
      height={Math.max(240, order.length * 24 + 60)}
      ariaLabel="Risk contributions"
    />
  );
}

function AllocationTable({
  d,
  current,
}: {
  d: OptimizeOut;
  current: Record<string, number> | null;
}) {
  type Row = {
    ticker: string;
    weight: number;
    current: number | null;
    trade: number | null;
    rc: number;
    mu: number | null;
    vol: number;
    sharpe: number | null;
    hist: number;
  };
  const rows = useMemo<Row[]>(() => {
    const assets = new Map(d.assets.map((a) => [a.ticker, a]));
    return d.allocation.map((a) => {
      const as = assets.get(a.ticker);
      const c = current ? (current[a.ticker] ?? 0) : null;
      return {
        ticker: a.ticker,
        weight: a.weight,
        current: c,
        trade: c == null ? null : a.weight - c,
        rc: a.risk_contribution_pct,
        mu: a.expected_return,
        vol: as?.volatility ?? NaN,
        sharpe: as?.sharpe ?? null,
        hist: as?.historical_mean ?? NaN,
      };
    });
  }, [d, current]);
  const maxW = Math.max(...rows.map((r) => Math.abs(r.weight)), 0.01);
  const cols: Column<Row>[] = [
    {
      key: "ticker",
      label: "Asset",
      render: (r) => (
        <Link to={`/ticker/${r.ticker}`} className="num op-tk">
          {r.ticker}
        </Link>
      ),
    },
    {
      key: "weight",
      label: "Weight",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      heat: { min: 0, max: maxW, diverging: false },
    },
    ...(current
      ? [
          {
            key: "current",
            label: "Current",
            numeric: true,
            format: (v: number) => fmtPct(v, 1),
          } as Column<Row>,
          {
            key: "trade",
            label: "Trade",
            numeric: true,
            format: (v: number) => fmtSignedPct(v, 1),
            color: "sign",
            info: {
              text: "Target weight minus current weight: positive = buy, negative = sell.",
            },
          } as Column<Row>,
        ]
      : []),
    {
      key: "rc",
      label: "Risk share",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      info: INFO.riskContribution,
    },
    {
      key: "mu",
      label: "Exp. return",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      info: {
        text: `Annual expected return the optimiser used (${RETURNS_LABEL[d.expected_returns.model]}).`,
      },
    },
    {
      key: "hist",
      label: "Hist. mean",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      hideBelow: 900,
      info: {
        text: "Plain arithmetic average annual return over the window, for comparison.",
      },
    },
    {
      key: "vol",
      label: "Vol",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      info: "vol",
      hideBelow: 600,
    },
    {
      key: "sharpe",
      label: "Sharpe",
      numeric: true,
      format: (v) => fmtNum(v, 2),
      info: "sharpe",
      hideBelow: 900,
    },
  ];
  return (
    <DataTable<Row>
      columns={cols}
      rows={rows}
      rowKey={(r) => r.ticker}
      defaultSort={{ key: "weight", dir: "desc" }}
    />
  );
}

function Bisection({
  steps,
}: {
  steps: {
    left: string[];
    right: string[];
    alpha: number;
    var_left: number;
    var_right: number;
  }[];
}) {
  return (
    <ol className="op-bisect">
      {steps.map((s, i) => (
        <li key={i}>
          <div className="op-bisect-bar" aria-hidden>
            <span style={{ width: `${s.alpha * 100}%` }} />
          </div>
          <div className="op-bisect-row small">
            <span>
              <span className="num">{fmtPct(s.alpha, 0)}</span> →{" "}
              {s.left.join(" ")}
            </span>
            <span className="subtle">
              {s.right.join(" ")} ←{" "}
              <span className="num">{fmtPct(1 - s.alpha, 0)}</span>
            </span>
          </div>
          <div className="op-bisect-var subtle">
            σ² <span className="num">{fmtNum(s.var_left, 4)}</span> vs{" "}
            <span className="num">{fmtNum(s.var_right, 4)}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}

function ExpectedChart({ d }: { d: OptimizeOut }) {
  const e = d.expected_returns;
  const order = useMemo(() => sortBy(d.universe.tickers, e.mu), [d, e]);
  const hist = useMemo(
    () => new Map(d.assets.map((a) => [a.ticker, a.historical_mean])),
    [d],
  );
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const se = e.standard_error;
        const main: Data = {
          type: "bar",
          name: RETURNS_LABEL[e.model],
          x: order,
          y: order.map((k) => e.mu[k]),
          marker: { color: t.categorical[0] },
          error_y: se
            ? {
                type: "data",
                array: order.map((k) => 2 * (se[k] ?? 0)),
                color: t.text3,
                thickness: 1.2,
                width: 5,
              }
            : undefined,
          customdata: se
            ? order.map((k) => [e.mu[k] - 2 * se[k], e.mu[k] + 2 * se[k]])
            : undefined,
          hovertemplate: se
            ? "<b>%{x}</b>  %{y:.1%}<br>±2 SE: %{customdata[0]:.1%} to %{customdata[1]:.1%}<extra></extra>"
            : `<b>%{x}</b>  %{y:.1%}<extra>${RETURNS_LABEL[e.model]}</extra>`,
        } as Data;
        if (e.model === "historical") return [main];
        return [
          main,
          {
            type: "bar",
            name: "Historical mean",
            x: order,
            y: order.map((k) => hist.get(k) ?? null),
            marker: { color: t.text3, opacity: 0.45 },
            hovertemplate: "<b>%{x}</b>  %{y:.1%}<extra>historical</extra>",
          } as Data,
        ];
      },
    [e, order, hist],
  );
  const layout = useMemo(
    () => ({
      barmode: "group",
      showlegend: e.model !== "historical",
      margin: { l: 8, r: 8, t: e.model !== "historical" ? 28 : 8, b: 28 },
      yaxis: { tickformat: ".0%", zeroline: true, side: "right" },
      xaxis: { type: "category", showspikes: false },
    }),
    [e.model],
  );
  return (
    <Chart
      data={data}
      layout={layout as never}
      height={280}
      ariaLabel="Expected returns"
    />
  );
}
