/**
 * Costs tab — POST /api/backtest/costs: the same positions re-priced at other proportional
 * cost levels (turnover does not depend on cost), with break-even costs.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, Field, Panel, type Column } from "../../components";
import { Icon } from "../../components/Icon";
import { fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import type { BaseBody } from "./config";
import { INFO } from "./info";
import { Verdict, finite, fmtSR } from "./shared";
import type { CostPoint, CostsOut } from "./types";

const DEFAULT_BPS = [0, 1, 2, 5, 10, 15, 20, 30, 50, 75, 100];

function parseBps(
  text: string,
  cap: number,
): { values: number[]; error: string | null } {
  const parts = text.split(/[\s,;]+/).filter(Boolean);
  const vals: number[] = [];
  for (const p of parts) {
    const v = Number(p);
    if (!Number.isFinite(v))
      return { values: [], error: `“${p}” is not a number` };
    if (v < 0 || v > 1000)
      return {
        values: [],
        error: "cost levels must be between 0 and 1,000 bps",
      };
    vals.push(v);
  }
  const u = [...new Set(vals)].sort((a, b) => a - b);
  if (u.length < 2)
    return { values: u, error: "give at least two cost levels" };
  if (u.length > cap) return { values: u, error: `at most ${cap} levels` };
  return { values: u, error: null };
}

export function CostsTab({
  base,
  cap,
}: {
  base: BaseBody | null;
  cap: number;
}) {
  const [text, setText] = useState(() =>
    [...new Set([...DEFAULT_BPS, base?.cost_bps ?? 5])]
      .sort((a, b) => a - b)
      .join(", "),
  );
  const parsed = parseBps(text, cap);
  const [submitted, setSubmitted] = useState<number[]>(parsed.values);
  const body = base ? { ...base, bps: submitted } : null;
  const q = useApiPost<CostsOut>("/backtest/costs", body, {
    enabled: !!body && submitted.length >= 2,
  });
  const changed = parsed.values.join() !== submitted.join();
  return (
    <div className="sl-tab">
      <section className="oc-panel sl-controls">
        <div className="sl-controls-intro">
          <div className="eyebrow">Cost sensitivity</div>
          <p className="small">
            The same trades priced at different one-way costs. A strategy whose
            edge vanishes at a few basis points only works for someone who
            trades for free. For liquid ETFs at a discount broker, 1–5 bp per
            trade is realistic; small caps or options cost far more.
          </p>
        </div>
        <div className="sl-controls-foot">
          <Field
            label="Cost levels (bps, one-way)"
            info={INFO.cost_bps}
            hint={
              parsed.error ? (
                <span className="loss">{parsed.error}</span>
              ) : (
                `${parsed.values.length} levels`
              )
            }
          >
            <input
              className="input num sl-values sl-values-wide"
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
              aria-label="Cost levels in basis points"
            />
          </Field>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!!parsed.error || !base || (!changed && !q.isError)}
            onClick={() => setSubmitted(parsed.values)}
          >
            <Icon name="scale" size={14} />{" "}
            {q.isFetching ? "Re-pricing…" : "Re-price"}
          </button>
        </div>
      </section>

      <Panel<CostsOut> query={q} skeletonHeight={220}>
        {(d) => <CostsVerdict d={d} yourBps={base?.cost_bps ?? null} />}
      </Panel>

      {!q.isError && q.data && (
        <>
          <div className="grid-2">
            <Panel<CostsOut>
              title="Sharpe vs trading cost"
              info={INFO.breakeven}
              subtitle="Net Sharpe at each cost level. Where the line crosses zero is the break-even cost; the dotted marker is your assumption."
              query={q}
              notes={[]}
              skeletonHeight={300}
            >
              {(d) => (
                <CostCurve
                  d={d}
                  metric="sharpe"
                  yourBps={base?.cost_bps ?? null}
                />
              )}
            </Panel>
            <Panel<CostsOut>
              title="CAGR vs trading cost"
              info={INFO.cagr}
              subtitle={`Net compound growth at each cost level against the benchmark’s buy-and-hold CAGR (grey).`}
              query={q}
              notes={[]}
              skeletonHeight={300}
            >
              {(d) => (
                <CostCurve
                  d={d}
                  metric="cagr"
                  yourBps={base?.cost_bps ?? null}
                />
              )}
            </Panel>
          </div>

          <Panel<CostsOut>
            title="Cost ladder"
            subtitle="Every level tested. Cost drag grows linearly with cost because turnover does not change."
            query={q}
            flush
            notes={[]}
            skeletonHeight={240}
          >
            {(d) => <CostTable d={d} yourBps={base?.cost_bps ?? null} />}
          </Panel>
        </>
      )}
    </div>
  );
}

function CostsVerdict({ d, yourBps }: { d: CostsOut; yourBps: number | null }) {
  const be = d.breakeven_bps_sharpe_zero;
  const bb = d.breakeven_bps_vs_benchmark;
  const at0 = d.curve.find((c) => c.cost_bps === 0);
  const head = finite(be)
    ? `The Sharpe reaches zero at ${fmtNum(be, be < 10 ? 1 : 0)} bp per trade.`
    : at0 && finite(at0.sharpe) && at0.sharpe <= 0
      ? "The Sharpe is not positive even when trading is free."
      : "The Sharpe stays positive at every cost up to 10,000 bp.";
  const margin =
    finite(be) && finite(yourBps) && yourBps > 0 ? be / yourBps : null;
  const tone = !finite(be)
    ? at0 && (at0.sharpe ?? 0) <= 0
      ? "loss"
      : "gain"
    : margin !== null && margin < 3
      ? "warn"
      : "gain";
  return (
    <Verdict
      eyebrow={`${d.strategy.name} · ${fmtMultiple(d.annual_turnover, 1)} annual turnover`}
      head={head}
      tone={tone}
      stats={[
        {
          label: "Break-even (Sharpe = 0)",
          value: finite(be) ? `${fmtNum(be, be < 10 ? 1 : 0)} bp` : "—",
          caption: finite(be) ? undefined : "not crossed below 10,000 bp",
          info: INFO.breakeven,
        },
        {
          label: `Break-even vs ${d.benchmark.ticker}`,
          value: finite(bb) ? `${fmtNum(bb, bb < 10 ? 1 : 0)} bp` : "—",
          caption: finite(bb)
            ? "CAGR falls to the benchmark’s"
            : d.curve[0] &&
                (d.curve[0].cagr ?? 0) < (d.benchmark.summary.cagr ?? 0)
              ? "trails it even at zero cost"
              : "stays ahead",
          info: INFO.breakeven,
        },
        {
          label: "Annual turnover",
          value: fmtMultiple(d.annual_turnover, 1),
          info: INFO.turnover,
        },
        {
          label: "Safety margin",
          value: margin !== null ? fmtMultiple(margin, 1) : "—",
          caption: finite(yourBps)
            ? `break-even ÷ your ${yourBps} bp`
            : undefined,
          info: {
            text: "How many times your assumed cost the strategy could absorb before its Sharpe hits zero. Below about 3× the result is fragile to real-world frictions.",
          },
        },
      ]}
    >
      <p>
        Each extra basis point of one-way cost removes about{" "}
        <span className="num">
          {fmtPct((d.annual_turnover ?? 0) / 10000, 2)}
        </span>{" "}
        of return a year (turnover × cost).{" "}
        {finite(be) && finite(yourBps)
          ? be > yourBps
            ? `At your ${yourBps} bp assumption the strategy keeps a positive Sharpe.`
            : `At your ${yourBps} bp assumption the Sharpe is already negative.`
          : ""}
      </p>
    </Verdict>
  );
}

function CostCurve({
  d,
  metric,
  yourBps,
}: {
  d: CostsOut;
  metric: "sharpe" | "cagr";
  yourBps: number | null;
}) {
  const bench = d.benchmark.summary[metric];
  const be =
    metric === "sharpe"
      ? d.breakeven_bps_sharpe_zero
      : d.breakeven_bps_vs_benchmark;
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => [
        {
          type: "scatter",
          mode: "lines+markers",
          name: metric === "sharpe" ? "Net Sharpe" : "Net CAGR",
          x: d.curve.map((c) => c.cost_bps),
          y: d.curve.map((c) => c[metric]),
          line: { color: t.categorical[0], width: 2.2 },
          marker: { size: 6, color: t.categorical[0] },
          hovertemplate: `%{x} bp → <b>%{y:${metric === "sharpe" ? ".2f" : ".2%"}}</b><extra></extra>`,
        } as Data,
      ],
    [d, metric],
  );
  const layout = useMemo(
    () => (t: Tokens) => {
      const xmax = Math.max(...d.curve.map((c) => c.cost_bps));
      const shapes: any[] = [];
      const annotations: any[] = [];
      if (finite(bench)) {
        shapes.push({
          type: "line",
          xref: "paper",
          x0: 0,
          x1: 1,
          y0: bench,
          y1: bench,
          line: { color: t.text3, width: 1.2 },
        });
        annotations.push({
          xref: "paper",
          x: 1,
          y: bench,
          xanchor: "right",
          yanchor: "bottom",
          text: `${d.benchmark.ticker} ${metric === "sharpe" ? bench.toFixed(2) : fmtPct(bench, 1)}`,
          showarrow: false,
          font: { size: 11, color: t.text3 },
        });
      }
      if (metric === "sharpe")
        shapes.push({
          type: "line",
          xref: "paper",
          x0: 0,
          x1: 1,
          y0: 0,
          y1: 0,
          line: { color: t.ruleStrong, width: 1, dash: "dot" },
        });
      if (finite(be) && be <= xmax * 1.02) {
        shapes.push({
          type: "line",
          yref: "paper",
          y0: 0,
          y1: 1,
          x0: be,
          x1: be,
          line: { color: t.loss, width: 1.5 },
        });
        annotations.push({
          x: be,
          yref: "paper",
          y: 1,
          yanchor: "bottom",
          xanchor: "left",
          xshift: 4,
          text: `break-even ${fmtNum(be, be < 10 ? 1 : 0)} bp`,
          showarrow: false,
          font: { size: 11, color: t.loss },
        });
      }
      if (finite(yourBps)) {
        shapes.push({
          type: "line",
          yref: "paper",
          y0: 0,
          y1: 1,
          x0: yourBps,
          x1: yourBps,
          line: { color: t.text2, width: 1, dash: "dot" },
        });
        annotations.push({
          x: yourBps,
          yref: "paper",
          y: 0.02,
          yanchor: "bottom",
          xanchor: "left",
          xshift: 4,
          text: `you: ${yourBps} bp`,
          showarrow: false,
          font: { size: 11, color: t.text2 },
        });
      }
      return {
        showlegend: false,
        hovermode: "closest",
        xaxis: {
          title: { text: "One-way cost (bp per unit traded)" },
          showspikes: false,
          zeroline: false,
        },
        yaxis: {
          tickformat: metric === "sharpe" ? ".2f" : ".0%",
          zeroline: false,
        },
        margin: { l: 52, r: 12, t: 28, b: 44 },
        shapes,
        annotations,
      };
    },
    [d, metric, bench, be, yourBps],
  );
  return <Chart data={data} layout={layout as any} height={300} />;
}

function CostTable({ d, yourBps }: { d: CostsOut; yourBps: number | null }) {
  const cols: Column<CostPoint>[] = [
    { key: "cost_bps", label: "Cost", numeric: true, format: (v) => `${v} bp` },
    {
      key: "sharpe",
      label: "Sharpe",
      numeric: true,
      format: (v) => fmtSR(v),
      color: "sign",
      info: INFO.sharpe,
    },
    {
      key: "cagr",
      label: "CAGR",
      numeric: true,
      format: (v) => fmtPct(v, 2),
      color: "sign",
    },
    {
      key: "annual_cost_drag",
      label: "Cost drag / yr",
      numeric: true,
      format: (v) => fmtPct(v, 2),
      info: INFO.cost_drag,
    },
    {
      key: "ann_vol",
      label: "Vol",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      hideBelow: 600,
    },
    {
      key: "max_drawdown",
      label: "Max DD",
      numeric: true,
      format: (v) => fmtPct(v, 1),
      hideBelow: 600,
    },
  ];
  return (
    <DataTable
      columns={cols}
      rows={d.curve}
      rowKey={(c) => c.cost_bps}
      isActive={(c) => c.cost_bps === yourBps}
    />
  );
}
