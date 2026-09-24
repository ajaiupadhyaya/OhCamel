/**
 * Walk-forward tab — POST /api/backtest/walkforward: re-optimise on a rolling (or anchored)
 * in-sample window, trade the winner on the next out-of-sample block, stitch the blocks.
 */
import { useMemo, useState } from "react";
import type { Data } from "plotly.js";
import {
  Chart,
  DataTable,
  Field,
  Panel,
  SegmentedControl,
  TimeSeriesChart,
  Toggle,
  type Column,
} from "../../components";
import { Icon } from "../../components/Icon";
import { fmtDate, fmtPct, fmtSignedPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { Tokens } from "../../lib/theme";
import { fmtParams, humanize, sweepableParams, type BaseBody } from "./config";
import { GridEditor, defaultAxes, gridFromAxes, type Axis } from "./GridEditor";
import { INFO } from "./info";
import { Bars, Verdict, finite, fmtSR } from "./shared";
import type { Fold, StrategySpec, WalkForwardOut } from "./types";

const OBJECTIVES = ["sharpe", "sortino", "cagr", "calmar"] as const;
type Objective = (typeof OBJECTIVES)[number];

interface WfSettings {
  grid: Record<string, number[]>;
  is_days: number;
  oos_days: number;
  anchored: boolean;
  objective: Objective;
}

export function WalkForwardTab({
  spec,
  base,
  maxCombos,
}: {
  spec: StrategySpec;
  base: BaseBody | null;
  maxCombos: number;
}) {
  const params = useMemo(() => sweepableParams(spec), [spec]);
  const [axes, setAxes] = useState<Axis[]>(() => defaultAxes(params, 1));
  const [isDays, setIsDays] = useState("756");
  const [oosDays, setOosDays] = useState("126");
  const [anchored, setAnchored] = useState(false);
  const [objective, setObjective] = useState<Objective>("sharpe");
  const draft = gridFromAxes(axes, params);
  const settings: WfSettings = {
    grid: draft.grid,
    is_days: Number(isDays),
    oos_days: Number(oosDays),
    anchored,
    objective,
  };
  const [submitted, setSubmitted] = useState<WfSettings>(settings);
  const body =
    base && params.length && Object.keys(submitted.grid).length
      ? { ...base, ...submitted }
      : null;
  const q = useApiPost<WalkForwardOut>("/backtest/walkforward", body, {
    enabled: !!body,
  });

  if (!params.length) {
    return (
      <div className="sl-tab">
        <div className="oc-panel sl-empty">
          <Icon name="grid" size={20} />
          <div>
            <div className="sl-empty-title">
              {spec.name} has no numeric parameters to re-optimise.
            </div>
            <p className="subtle small">
              Walk-forward analysis chooses parameters on past data and tests
              them on the next block. With nothing to choose, the backtest
              itself is already out-of-sample.
            </p>
          </div>
        </div>
      </div>
    );
  }
  const tooMany = draft.combos > maxCombos;
  const changed = JSON.stringify(settings) !== JSON.stringify(submitted);

  return (
    <div className="sl-tab">
      <section className="oc-panel sl-controls">
        <div className="sl-controls-intro">
          <div className="eyebrow">Walk-forward</div>
          <p className="small">
            The honest version of parameter optimisation. At every step the
            parameters with the best in-sample{" "}
            {objective === "cagr" ? "CAGR" : humanize(objective)} over the
            trailing window are chosen, then traded — untouched — over the next
            block. Stitching the blocks gives a track record in which every
            decision used only data available at the time.
          </p>
        </div>
        <GridEditor axes={axes} onChange={setAxes} params={params} />
        <div className="sl-controls-foot">
          <Field
            label="In-sample window"
            info={{
              text: "Sessions of history used to choose the parameters at each step (252 ≈ 1 year).",
            }}
          >
            <SegmentedControl
              size="sm"
              ariaLabel="In-sample sessions"
              options={[
                { value: "252", label: "1Y" },
                { value: "504", label: "2Y" },
                { value: "756", label: "3Y" },
                { value: "1008", label: "4Y" },
              ]}
              value={isDays}
              onChange={setIsDays}
            />
          </Field>
          <Field
            label="Out-of-sample block"
            info={{
              text: "Sessions each choice is traded before re-optimising (63 ≈ a quarter).",
            }}
          >
            <SegmentedControl
              size="sm"
              ariaLabel="Out-of-sample sessions"
              options={[
                { value: "63", label: "3M" },
                { value: "126", label: "6M" },
                { value: "252", label: "1Y" },
              ]}
              value={oosDays}
              onChange={setOosDays}
            />
          </Field>
          <Field
            label="Objective"
            info={{
              text: "What ‘best in-sample’ means when choosing parameters.",
            }}
          >
            <SegmentedControl
              size="sm"
              ariaLabel="Objective"
              options={OBJECTIVES.map((o) => ({
                value: o,
                label: o === "cagr" ? "CAGR" : humanize(o),
              }))}
              value={objective}
              onChange={setObjective}
            />
          </Field>
          <Field
            label="Window"
            info={{
              text: "Rolling uses only the last N sessions; anchored grows from the start of the data, so later choices use more history.",
            }}
          >
            <Toggle
              label={anchored ? "Anchored" : "Rolling"}
              checked={anchored}
              onChange={setAnchored}
            />
          </Field>
          <div className="sl-run-status small">
            {draft.error ? (
              <span className="loss">{draft.error}</span>
            ) : tooMany ? (
              <span className="loss">
                {draft.combos} combinations — the cap is {maxCombos}.
              </span>
            ) : (
              <span className="subtle">
                <span className="num">{draft.combos}</span> candidates per fold
                {changed ? " · not run yet" : ""}
              </span>
            )}
          </div>
          <button
            type="button"
            className="btn btn-primary"
            disabled={
              !!draft.error || tooMany || !base || (!changed && !q.isError)
            }
            onClick={() => setSubmitted(settings)}
          >
            <Icon name="refresh" size={14} />{" "}
            {q.isFetching ? "Running…" : "Run walk-forward"}
          </button>
        </div>
      </section>

      <Panel<WalkForwardOut> query={q} skeletonHeight={240}>
        {(d) => <WfVerdict d={d} />}
      </Panel>

      {!q.isError && q.data && (
        <>
          <Panel<WalkForwardOut>
            title="Out-of-sample track record"
            info={INFO.walk_forward}
            subtitle="Growth of $1 from stitching every out-of-sample block, against the fixed parameters you set and buy-and-hold of the benchmark over the same dates."
            query={q}
            notes={[]}
            skeletonHeight={380}
          >
            {(d) => <WfEquity d={d} />}
          </Panel>

          <div className="grid-2">
            <Panel<WalkForwardOut>
              title="In-sample promise vs out-of-sample delivery"
              info={INFO.wfe}
              subtitle="For each fold, the Sharpe the chosen parameters showed in-sample and what they earned in the next block. Consistently shorter right-hand bars are the cost of optimisation."
              query={q}
              notes={[]}
              skeletonHeight={280}
            >
              {(d) => (
                <Bars
                  series={[
                    {
                      name: "In-sample",
                      x: d.folds.map((_, i) => `F${i + 1}`),
                      y: d.folds.map((f) => f.is_sharpe),
                      color: "var(--text-3)",
                    },
                    {
                      name: "Out-of-sample",
                      x: d.folds.map((_, i) => `F${i + 1}`),
                      y: d.folds.map((f) => f.oos_sharpe),
                      color: "var(--c1)",
                    },
                  ]}
                  height={280}
                />
              )}
            </Panel>
            <Panel<WalkForwardOut>
              title="The schedule"
              subtitle="Each row is a fold: the in-sample window used to choose (pale) and the block it was traded (solid), labelled with the parameters chosen."
              query={q}
              notes={[]}
              skeletonHeight={280}
            >
              {(d) => <FoldTimeline d={d} />}
            </Panel>
          </div>

          <Panel<WalkForwardOut>
            title="Folds"
            subtitle="The parameters chosen at each step and how they fared. Frequent switching is a sign the optimum is noise."
            query={q}
            flush
            notes={[]}
            skeletonHeight={240}
          >
            {(d) => <FoldTable d={d} />}
          </Panel>
        </>
      )}
    </div>
  );
}

function WfVerdict({ d }: { d: WalkForwardOut }) {
  const wfe = d.walk_forward_efficiency;
  const ref = d.reference;
  const oos = d.oos_summary;
  // "tie" when the two Sharpes agree to within 0.02 (they print the same at 2 dp).
  const beatRef: boolean | "tie" | null =
    ref && finite(oos.sharpe) && finite(ref.summary.sharpe)
      ? Math.abs(oos.sharpe - ref.summary.sharpe) < 0.02
        ? "tie"
        : oos.sharpe > ref.summary.sharpe
      : null;
  const tone = !finite(wfe)
    ? "neutral"
    : wfe >= 0.7
      ? "gain"
      : wfe >= 0.3
        ? "warn"
        : "loss";
  const head = !finite(wfe)
    ? "Walk-forward efficiency is undefined for this run."
    : wfe >= 0.7
      ? `Re-optimising kept ${fmtPct(wfe, 0)} of the in-sample Sharpe out of sample.`
      : wfe >= 0
        ? `Only ${fmtPct(wfe, 0)} of the in-sample Sharpe survived out of sample.`
        : "The out-of-sample Sharpe turned negative: the in-sample optimum did not carry over.";
  return (
    <Verdict
      eyebrow={`${d.folds.length} folds · ${fmtDate(d.folds[0]?.oos_start)} – ${fmtDate(d.folds.at(-1)?.oos_end)} · ${d.combos.length} candidates`}
      head={head}
      tone={tone}
      stats={[
        {
          label: "OOS Sharpe",
          value: fmtSR(d.oos_sharpe),
          caption: `mean IS ${fmtSR(d.mean_is_sharpe)}`,
          info: INFO.sharpe,
        },
        {
          label: "WF efficiency",
          value: finite(wfe) ? fmtPct(wfe, 0) : "—",
          info: INFO.wfe,
          tone: tone === "neutral" ? "" : tone,
        },
        {
          label: "OOS CAGR",
          value: fmtPct(oos.cagr, 1),
          caption: ref
            ? `fixed params ${fmtPct(ref.summary.cagr, 1)}`
            : undefined,
          info: INFO.cagr,
        },
        {
          label: "Fixed params",
          value: ref ? fmtSR(ref.summary.sharpe) : "—",
          caption: ref ? fmtParams(ref.params) : "not in the grid",
          info: {
            text: "The same dates traded with the parameters you set in the Rule panel, never re-optimised. Shown only when your setting is one of the grid values.",
          },
        },
        {
          label: "Distinct choices",
          value: `${d.distinct_choices} of ${d.combos.length}`,
          caption: `over ${d.folds.length} folds`,
          info: {
            text: "How many different parameter sets the walk-forward picked. Many different picks mean the in-sample optimum wanders — often a sign it is noise.",
          },
        },
      ]}
    >
      <p>
        Choosing parameters on the trailing window and trading them blind
        produced an out-of-sample Sharpe of{" "}
        <span className="num">{fmtSR(d.oos_sharpe)}</span>, against{" "}
        <span className="num">{fmtSR(d.benchmark_summary.sharpe)}</span> for the
        benchmark over the same dates.{" "}
        {beatRef === null
          ? ""
          : beatRef === "tie"
            ? "Simply keeping your fixed parameters did about as well — re-optimising neither added nor lost value here."
            : beatRef
              ? "It did at least as well as simply keeping your fixed parameters — re-optimising added value here."
              : "Simply keeping your fixed parameters would have done better — the optimisation chased noise."}
      </p>
    </Verdict>
  );
}

function WfEquity({ d }: { d: WalkForwardOut }) {
  const [log, setLog] = useState(true);
  const x = d.equity.index as string[];
  const series = [
    {
      name: "Walk-forward (OOS)",
      x,
      y: d.equity.data.walk_forward,
      width: 2.2,
    },
    ...(d.equity.data.base_params
      ? [
          {
            name: `Fixed: ${fmtParams(d.reference?.params)}`,
            x,
            y: d.equity.data.base_params,
            color: "var(--c2)",
            width: 1.4,
          },
        ]
      : []),
    {
      name: "Benchmark",
      x,
      y: d.equity.data.benchmark,
      color: "var(--text-3)",
      width: 1.3,
    },
  ];
  return (
    <>
      <div className="sl-chart-tools">
        <Toggle label="Log scale" checked={log} onChange={setLog} />
      </div>
      <TimeSeriesChart
        series={series}
        yFormat="usd"
        digits={2}
        logY={log}
        baseline={1}
        height={360}
        layout={{ yaxis: { tickformat: ",.2f" } } as any}
      />
    </>
  );
}

function FoldTimeline({ d }: { d: WalkForwardOut }) {
  const data = useMemo(
    () =>
      (t: Tokens): Data[] => {
        const ys = d.folds.map((_, i) => `F${i + 1}`);
        const dur = (a: string, b: string) =>
          new Date(b).getTime() - new Date(a).getTime();
        return [
          {
            type: "bar",
            orientation: "h",
            name: "In-sample",
            y: ys,
            base: d.folds.map((f) => f.is_start),
            x: d.folds.map((f) => dur(f.is_start, f.is_end)),
            marker: { color: t.categorical[0], opacity: 0.22 },
            customdata: d.folds.map((f) => [
              fmtDate(f.is_start),
              fmtDate(f.is_end),
              fmtSR(f.is_sharpe),
            ]),
            hovertemplate:
              "In-sample %{customdata[0]} → %{customdata[1]}<br>Sharpe <b>%{customdata[2]}</b><extra></extra>",
          },
          {
            type: "bar",
            orientation: "h",
            name: "Out-of-sample",
            y: ys,
            base: d.folds.map((f) => f.oos_start),
            x: d.folds.map((f) => dur(f.oos_start, f.oos_end)),
            marker: {
              color: d.folds.map((f) =>
                (f.oos_return ?? 0) < 0 ? t.loss : t.gain,
              ),
            },
            text: d.folds.map((f) => fmtParams(f.params)),
            textposition: "outside",
            textfont: { family: t.fontMono, size: 10, color: t.text2 },
            cliponaxis: false,
            customdata: d.folds.map((f) => [
              fmtDate(f.oos_start),
              fmtDate(f.oos_end),
              fmtSignedPct(f.oos_return, 1),
              fmtParams(f.params),
            ]),
            hovertemplate:
              "%{customdata[3]}<br>OOS %{customdata[0]} → %{customdata[1]}<br>Return <b>%{customdata[2]}</b><extra></extra>",
          },
        ] as unknown as Data[];
      },
    [d],
  );
  return (
    <Chart
      data={data}
      layout={
        {
          barmode: "overlay",
          showlegend: false,
          xaxis: { type: "date" },
          yaxis: { type: "category", autorange: "reversed", showgrid: false },
          margin: { l: 36, r: 90, t: 8, b: 28 },
          bargap: 0.35,
        } as any
      }
      height={Math.max(240, d.folds.length * 24 + 50)}
    />
  );
}

function FoldTable({ d }: { d: WalkForwardOut }) {
  const cols: Column<Fold & { i: number }>[] = [
    {
      key: "i",
      label: "Fold",
      render: (f) => <span className="num">F{f.i + 1}</span>,
    },
    {
      key: "params",
      label: "Chosen",
      render: (f) => <span className="num">{fmtParams(f.params)}</span>,
      sortable: false,
    },
    {
      key: "is_start",
      label: "In-sample",
      render: (f) => (
        <span className="subtle">
          {fmtDate(f.is_start, "month")} – {fmtDate(f.is_end, "month")}
        </span>
      ),
      hideBelow: 900,
    },
    {
      key: "is_sharpe",
      label: "IS Sharpe",
      numeric: true,
      format: (v) => fmtSR(v),
    },
    {
      key: "oos_start",
      label: "Traded",
      render: (f) => (
        <span>
          {fmtDate(f.oos_start, "month")} – {fmtDate(f.oos_end, "month")}
        </span>
      ),
      hideBelow: 600,
    },
    {
      key: "oos_sharpe",
      label: "OOS Sharpe",
      numeric: true,
      format: (v) => fmtSR(v),
      color: "sign",
    },
    {
      key: "oos_return",
      label: "OOS return",
      numeric: true,
      format: (v) => fmtSignedPct(v, 1),
      color: "sign",
    },
    {
      key: "switch_cost",
      label: "Switch cost",
      numeric: true,
      format: (v) => (v ? fmtPct(v, 2) : "—"),
      hideBelow: 1200,
      info: {
        text: "Cost of moving from the previous fold's weights to the new parameters' weights at the switch.",
      },
    },
  ];
  return (
    <DataTable
      columns={cols}
      rows={d.folds.map((f, i) => ({ ...f, i }))}
      rowKey={(f) => f.i}
    />
  );
}
