/**
 * Frontier tab: the constrained efficient frontier in (volatility, return) space with the
 * capital market line, every asset, the other allocation methods, your current portfolio
 * and the chosen optimum. Hover any point for its weights.
 */
import { useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type { Data } from "plotly.js";
import { Chart, DataTable, Toggle, type Column } from "../../components";
import type { ApiError } from "../../lib/api";
import { fmtNum, fmtPct } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import { INFO, METHOD_SHORT } from "./info";
import { QPanel, foldTop, useOpt, weightsLine } from "./shared";
import type { FrontierOut, MethodName, OptimizeOut } from "./types";

type Point = {
  key: string;
  name: string;
  ret: number;
  vol: number;
  sharpe: number | null;
  weights?: Record<string, number>;
  kind: string;
};

export function FrontierTab({
  q,
  opt,
  current,
}: {
  q: UseQueryResult<FrontierOut, ApiError>;
  opt: UseQueryResult<OptimizeOut, ApiError>;
  current: UseQueryResult<OptimizeOut, ApiError> | null;
}) {
  const { spec } = useOpt();
  const [showAssets, setShowAssets] = useState(true);
  const [showMethods, setShowMethods] = useState(true);
  const chosen = opt.data && !opt.isError ? opt.data.result : null;
  const cur = current?.data && !current.isError ? current.data.result : null;

  return (
    <div className="stack-lg">
      <QPanel<FrontierOut>
        q={q}
        title="Efficient frontier"
        info={INFO.frontier}
        subtitle="Each point is a portfolio: further right is riskier, higher up earns more (by the model's own estimates). The curve is the best any mix can do under your constraints; the straight line adds borrowing and lending at the risk-free rate. Hover for weights."
        skeletonHeight={520}
        actions={
          <>
            <Toggle
              label="Assets"
              checked={showAssets}
              onChange={setShowAssets}
            />
            <Toggle
              label="Other methods"
              checked={showMethods}
              onChange={setShowMethods}
            />
          </>
        }
      >
        {(d) => (
          <FrontierChart
            d={d}
            chosen={
              chosen
                ? {
                    label: spec?.label ?? METHOD_SHORT[chosen.method],
                    ret: chosen.expected_return,
                    vol: chosen.volatility,
                    sharpe: chosen.sharpe,
                    weights: chosen.weights,
                  }
                : null
            }
            current={
              cur
                ? {
                    ret: cur.expected_return,
                    vol: cur.volatility,
                    sharpe: cur.sharpe,
                    weights: cur.weights,
                  }
                : null
            }
            showAssets={showAssets}
            showMethods={showMethods}
          />
        )}
      </QPanel>

      {!q.isError && (
        <div className="op-fr-grid">
          <QPanel<FrontierOut>
            q={q}
            title="Portfolios on the map"
            info={{
              text: "Model-estimated annual return, volatility and Sharpe ratio for each marked portfolio. The same estimates drive every row, so they are comparable to each other — not to realised performance.",
            }}
            subtitle="Ex-ante statistics of every marked portfolio"
            flush
            skeletonHeight={280}
            notes={[]}
            provenance={[]}
          >
            {(d) => (
              <PointsTable
                d={d}
                chosen={
                  chosen
                    ? {
                        method: chosen.method,
                        name: `Chosen · ${chosen.method ? METHOD_SHORT[chosen.method] : ""}`,
                        ret: chosen.expected_return,
                        vol: chosen.volatility,
                        sharpe: chosen.sharpe,
                      }
                    : null
                }
                current={
                  cur
                    ? {
                        ret: cur.expected_return,
                        vol: cur.volatility,
                        sharpe: cur.sharpe,
                      }
                    : null
                }
              />
            )}
          </QPanel>
          <QPanel<FrontierOut>
            q={q}
            title="How the weights change along the frontier"
            info={{
              text: "Each vertical slice is one frontier portfolio. Moving right (more risk) the optimiser rotates from the lowest-volatility assets into the highest-return ones. Abrupt switches are a sign of fragile, estimate-driven weights.",
            }}
            subtitle="Weights of each frontier portfolio, by its volatility"
            skeletonHeight={280}
            notes={[]}
            provenance={[]}
          >
            {(d) => <CompositionChart d={d} />}
          </QPanel>
        </div>
      )}
    </div>
  );
}

function FrontierChart({
  d,
  chosen,
  current,
  showAssets,
  showMethods,
}: {
  d: FrontierOut;
  chosen: {
    label: string;
    ret: number | null;
    vol: number;
    sharpe: number | null;
    weights: Record<string, number>;
  } | null;
  current: {
    ret: number | null;
    vol: number;
    sharpe: number | null;
    weights: Record<string, number>;
  } | null;
  showAssets: boolean;
  showMethods: boolean;
}) {
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const sr = (s: number | null | undefined) =>
          s == null ? "" : `<br>Sharpe ${fmtNum(s, 2)}`;
        const out: Data[] = [];
        const f = d.frontier;
        out.push({
          type: "scatter",
          mode: "lines",
          name: "Efficient frontier",
          x: f.map((p) => p.vol),
          y: f.map((p) => p.ret),
          line: {
            color: t.categorical[0],
            width: 2.6,
            shape: "spline",
            smoothing: 0.6,
          },
          text: f.map(
            (p) =>
              `<b>Frontier</b><br>return ${fmtPct(p.ret, 1)} · vol ${fmtPct(p.vol, 1)}${sr(p.sharpe)}<br><br>${weightsLine(p.weights)}`,
          ),
          hovertemplate: "%{text}<extra></extra>",
        } as Data);
        if (d.cml && d.tangency) {
          out.push({
            type: "scatter",
            mode: "lines",
            name: "Capital market line",
            x: d.cml.map((p) => p.vol),
            y: d.cml.map((p) => p.ret),
            line: { color: t.text2, width: 1.2, dash: "dash" },
            hovertemplate: `<b>CML</b><br>return %{y:.1%} · vol %{x:.1%}<extra></extra>`,
          } as Data);
        }
        if (showAssets) {
          out.push({
            type: "scatter",
            mode: "text+markers",
            name: "Assets",
            x: d.assets.map((a) => a.vol),
            y: d.assets.map((a) => a.ret),
            text: d.assets.map((a) => a.ticker),
            textposition: "top center",
            textfont: { family: t.fontMono, size: 10, color: t.text3 },
            marker: {
              size: 7,
              color: t.surface,
              line: { color: t.text3, width: 1.4 },
            },
            hovertemplate:
              "<b>%{text}</b> (100%)<br>return %{y:.1%} · vol %{x:.1%}<extra></extra>",
          } as Data);
        }
        if (showMethods) {
          d.overlay
            .filter((o) => o.ret != null && o.vol != null)
            .forEach((o, i) => {
              out.push({
                type: "scatter",
                mode: "markers",
                name: METHOD_SHORT[o.method],
                x: [o.vol],
                y: [o.ret],
                marker: {
                  size: 11,
                  symbol: "diamond",
                  color: t.categorical[(i + 2) % 8],
                  line: { color: t.surface, width: 1.2 },
                },
                text: [
                  `<b>${o.label}</b><br>return ${fmtPct(o.ret, 1)} · vol ${fmtPct(o.vol, 1)}${sr(o.sharpe)}<br><br>${weightsLine(o.weights)}`,
                ],
                hovertemplate: "%{text}<extra></extra>",
              } as Data);
            });
        }
        if (
          !showMethods ||
          !d.overlay.some((o) => o.method === "min_variance" && o.vol != null)
        )
          out.push({
            type: "scatter",
            mode: "markers",
            name: "Min variance",
            x: [d.gmv.vol],
            y: [d.gmv.ret],
            marker: {
              size: 11,
              symbol: "circle",
              color: t.categorical[0],
              line: { color: t.surface, width: 2 },
            },
            text: [
              `<b>Global minimum variance</b><br>return ${fmtPct(d.gmv.ret, 1)} · vol ${fmtPct(d.gmv.vol, 1)}<br><br>${weightsLine(d.gmv.weights)}`,
            ],
            hovertemplate: "%{text}<extra></extra>",
          } as Data);
        if (d.tangency) {
          out.push({
            type: "scatter",
            mode: "markers",
            name: "Tangency (max Sharpe)",
            x: [d.tangency.vol],
            y: [d.tangency.ret],
            marker: {
              size: 16,
              symbol: "star",
              color: t.categorical[1],
              line: { color: t.surface, width: 1.2 },
            },
            text: [
              `<b>Tangency portfolio</b><br>return ${fmtPct(d.tangency.ret, 1)} · vol ${fmtPct(d.tangency.vol, 1)}${sr(d.tangency.sharpe)}<br><br>${weightsLine(d.tangency.weights)}`,
            ],
            hovertemplate: "%{text}<extra></extra>",
          } as Data);
        }
        if (current && current.ret != null) {
          out.push({
            type: "scatter",
            mode: "text+markers",
            name: "Current portfolio",
            x: [current.vol],
            y: [current.ret],
            text: ["Current"],
            textposition: "bottom center",
            textfont: { size: 11, color: t.text },
            marker: {
              size: 13,
              symbol: "square",
              color: t.text,
              line: { color: t.surface, width: 2 },
            },
            customdata: [
              `<b>Your current portfolio</b><br>return ${fmtPct(current.ret, 1)} · vol ${fmtPct(current.vol, 1)}${sr(current.sharpe)}<br><br>${weightsLine(current.weights)}`,
            ],
            hovertemplate: "%{customdata}<extra></extra>",
          } as Data);
        }
        if (chosen && chosen.ret != null) {
          out.push({
            type: "scatter",
            mode: "text+markers",
            name: `Chosen: ${chosen.label}`,
            x: [chosen.vol],
            y: [chosen.ret],
            text: ["Chosen"],
            textposition: "middle right",
            textfont: { size: 11, color: t.text },
            marker: {
              size: 22,
              symbol: "circle-open",
              color: t.text,
              line: { width: 2.2 },
            },
            customdata: [
              `<b>Chosen: ${chosen.label}</b><br>return ${fmtPct(chosen.ret, 1)} · vol ${fmtPct(chosen.vol, 1)}${sr(chosen.sharpe)}<br><br>${weightsLine(chosen.weights)}`,
            ],
            hovertemplate: "%{customdata}<extra></extra>",
          } as Data);
        }
        return out;
      },
    [d, chosen, current, showAssets, showMethods],
  );
  const layout = useMemo(
    () => (t: Tokens) => ({
      hovermode: "closest",
      margin: { l: 8, r: 12, t: 36, b: 44 },
      legend: {
        orientation: "h",
        y: 1.02,
        yanchor: "bottom",
        x: 0,
        font: { size: 11 },
      },
      hoverlabel: { align: "left" },
      xaxis: {
        title: { text: "Volatility (annual, ex-ante)" },
        tickformat: ".0%",
        rangemode: "tozero",
        showspikes: false,
        zeroline: false,
        showgrid: true,
        gridcolor: t.rule,
      },
      yaxis: {
        title: { text: "Expected return (annual)" },
        tickformat: ".0%",
        zeroline: true,
        side: "left",
      },
    }),
    [],
  );
  return (
    <Chart
      data={data}
      layout={layout as never}
      height={540}
      ariaLabel="Efficient frontier"
    />
  );
}

function PointsTable({
  d,
  chosen,
  current,
}: {
  d: FrontierOut;
  chosen: {
    method?: MethodName;
    name: string;
    ret: number | null;
    vol: number;
    sharpe: number | null;
  } | null;
  current: { ret: number | null; vol: number; sharpe: number | null } | null;
}) {
  const rows = useMemo<(Point & { error?: string })[]>(() => {
    const out: (Point & { error?: string })[] = [];
    if (chosen && chosen.ret != null)
      out.push({
        key: "chosen",
        name: chosen.name,
        ret: chosen.ret,
        vol: chosen.vol,
        sharpe: chosen.sharpe,
        kind: "chosen",
      });
    if (current && current.ret != null)
      out.push({
        key: "current",
        name: "Current portfolio",
        ret: current.ret,
        vol: current.vol,
        sharpe: current.sharpe,
        kind: "current",
      });
    if (d.tangency)
      out.push({
        key: "tangency",
        name: "Tangency",
        ret: d.tangency.ret,
        vol: d.tangency.vol,
        sharpe: d.tangency.sharpe ?? null,
        kind: "frontier",
      });
    if (!d.overlay.some((o) => o.method === "min_variance" && o.vol != null))
      out.push({
        key: "gmv",
        name: "Global minimum variance",
        ret: d.gmv.ret,
        vol: d.gmv.vol,
        sharpe:
          d.tangency && d.risk_free.annual != null
            ? (d.gmv.ret - d.risk_free.annual) / d.gmv.vol
            : null,
        kind: "frontier",
      });
    for (const o of d.overlay)
      out.push({
        key: o.method,
        name: METHOD_SHORT[o.method],
        ret: o.ret ?? NaN,
        vol: o.vol ?? NaN,
        sharpe: o.sharpe ?? null,
        kind: "method",
        error: o.error,
      });
    return out;
  }, [d, chosen, current]);
  const cols: Column<Point & { error?: string }>[] = [
    {
      key: "name",
      label: "Portfolio",
      render: (r) =>
        r.error ? (
          <span title={r.error}>
            {r.name} <span className="badge warn">failed</span>
          </span>
        ) : (
          <span
            className={
              r.kind === "chosen" || r.kind === "current" ? "op-strong" : ""
            }
          >
            {r.name}
          </span>
        ),
    },
    {
      key: "ret",
      label: "Return",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      info: INFO.exAnteReturn,
    },
    {
      key: "vol",
      label: "Vol",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      info: INFO.exAnteVol,
    },
    {
      key: "sharpe",
      label: "Sharpe",
      numeric: true,
      format: (v) => fmtNum(v, 2),
      info: INFO.exAnteSharpe,
    },
  ];
  return <DataTable columns={cols} rows={rows} rowKey={(r) => r.key} />;
}

function CompositionChart({ d }: { d: FrontierOut }) {
  const tickers = d.universe.tickers;
  const { keep, other } = useMemo(
    () =>
      foldTop(tickers, (t) =>
        Math.max(...d.frontier.map((p) => Math.abs(p.weights[t] ?? 0))),
      ),
    [d, tickers],
  );
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const x = d.frontier.map((p) => p.vol);
        const series = keep.map((k, i) => ({
          name: k,
          y: d.frontier.map((p) => p.weights[k] ?? 0),
          color: t.categorical[i % 8],
        }));
        if (other.length)
          series.push({
            name: `Other (${other.length})`,
            y: d.frontier.map((p) =>
              other.reduce((a, k) => a + (p.weights[k] ?? 0), 0),
            ),
            color: t.text3,
          });
        return series.map(
          (s) =>
            ({
              type: "scatter",
              mode: "lines",
              name: s.name,
              x,
              y: s.y,
              stackgroup: "w",
              line: { width: 0.6, color: s.color },
              fillcolor: s.color,
              hovertemplate: `<b>${s.name}</b> %{y:.1%}<extra></extra>`,
            }) as Data,
        );
      },
    [d, keep, other],
  );
  const longOnly = useMemo(
    () =>
      d.frontier.every((p) =>
        Object.values(p.weights).every((v) => v >= -1e-6),
      ),
    [d],
  );
  const layout = useMemo(
    () => ({
      hovermode: "x unified",
      margin: { l: 8, r: 8, t: 30, b: 40 },
      legend: {
        orientation: "h",
        y: 1.02,
        yanchor: "bottom",
        font: { size: 11 },
      },
      xaxis: {
        title: { text: "Frontier volatility" },
        tickformat: ".0%",
        showspikes: true,
        hoverformat: ".1%",
      },
      yaxis: {
        tickformat: ".0%",
        range: longOnly ? [0, 1] : undefined,
        side: "right",
      },
    }),
    [longOnly],
  );
  return (
    <Chart
      data={data}
      layout={layout as never}
      height={300}
      ariaLabel="Frontier weight composition"
    />
  );
}
