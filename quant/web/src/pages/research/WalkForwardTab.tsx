/**
 * WALK-FWD (POST /api/backtest/walkforward): re-optimise on a rolling (or anchored) in-sample
 * window, trade the pick blind over the next block, stitch the blocks. Verdict first: the
 * charter's holdout gate (the stitched out-of-sample record must be positive) and the PSR;
 * ADVISORY at best, since DSR and the regime windows are the farm's.
 */
import { useMemo, useState } from "react";
import { DataTable, Field, Panel, SegmentedControl, Toggle, type Column } from "../../components";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { fmtDate, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import { humanize, sweepableParams, type BaseBody } from "./config";
import { walkForwardVerdict } from "./derive";
import { GridEditor, defaultAxes, gridFromAxes, type Axis } from "./GridEditor";
import { INFO } from "./info";
import { Readline, VerdictBlock, finite, fmtSR, paramLine } from "./shared";
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

export function WalkForwardTab({ spec, base, maxCombos }: { spec: StrategySpec; base: BaseBody | null; maxCombos: number }) {
  const params = useMemo(() => sweepableParams(spec), [spec]);
  const [axes, setAxes] = useState<Axis[]>(() => defaultAxes(params, 1));
  const [isDays, setIsDays] = useState("756");
  const [oosDays, setOosDays] = useState("126");
  const [anchored, setAnchored] = useState(false);
  const [objective, setObjective] = useState<Objective>("sharpe");
  const draft = gridFromAxes(axes, params);
  const settings: WfSettings = { grid: draft.grid, is_days: Number(isDays), oos_days: Number(oosDays), anchored, objective };
  const [submitted, setSubmitted] = useState<WfSettings>(settings);
  const body = base && params.length && Object.keys(submitted.grid).length ? { ...base, ...submitted } : null;
  const q = useApiPost<WalkForwardOut>("/backtest/walkforward", body, { enabled: !!body });

  if (!params.length)
    return (
      <div className="sl-tab">
        <div className="sl-empty">
          <Absent reason="NO NUMERIC PARAMETERS · THE BACKTEST IS OUT-OF-SAMPLE" source={spec.key} />
        </div>
      </div>
    );

  const tooMany = draft.combos > maxCombos;
  const changed = JSON.stringify(settings) !== JSON.stringify(submitted);
  const asOf = q.data?.folds.at(-1)?.oos_end;
  const cell = { query: q, asOf, notes: [] as string[] };

  return (
    <div className="sl-tab">
      <Panel
        title={
          <>
            Walk-forward · setup
            <Note n={4} to="wfo-costs" />
          </>
        }
        actions={
          <button type="button" className={`btn btn-sm ${changed || q.isError ? "btn-primary" : ""}`} disabled={!!draft.error || tooMany || !base || q.isFetching || (!changed && !q.isError)} onClick={() => setSubmitted(settings)}>
            {q.isFetching ? "RUNNING" : changed || q.isError ? "RUN" : "CURRENT"}
          </button>
        }
      >
        <div className="sl-controls">
          <GridEditor axes={axes} onChange={setAxes} params={params} />
          <div className="sl-controls-row">
            <Field label="In-sample">
              <SegmentedControl size="sm" ariaLabel="In-sample sessions" options={[{ value: "252", label: "1Y" }, { value: "504", label: "2Y" }, { value: "756", label: "3Y" }, { value: "1008", label: "4Y" }]} value={isDays} onChange={setIsDays} />
            </Field>
            <Field label="Out-of-sample">
              <SegmentedControl size="sm" ariaLabel="Out-of-sample sessions" options={[{ value: "63", label: "3M" }, { value: "126", label: "6M" }, { value: "252", label: "1Y" }]} value={oosDays} onChange={setOosDays} />
            </Field>
            <Field label="Objective">
              <SegmentedControl size="sm" ariaLabel="Objective" options={OBJECTIVES.map((o) => ({ value: o, label: o === "sharpe" ? "SR" : humanize(o) }))} value={objective} onChange={setObjective} />
            </Field>
            <Field label="Window">
              <Toggle label="ANCHORED" checked={anchored} onChange={setAnchored} />
            </Field>
          </div>
          <Readline
            items={[
              { k: "CANDIDATES", v: draft.error ? "—" : `${draft.combos} / ${maxCombos}`, tone: tooMany ? "loss" : "" },
              draft.error ? { k: "INPUT", v: draft.error, tone: "loss" } : null,
              changed && !draft.error && { k: "INPUTS", v: "CHANGED", tone: "loss" },
            ]}
          />
        </div>
      </Panel>

      <Panel<WalkForwardOut> {...cell} notes={undefined} title="Verdict · walk-forward" skeletonHeight={280}>
        {(d) => <WfVerdict d={d} />}
      </Panel>

      {!q.isError && q.data && (
        <>
          <Panel<WalkForwardOut> {...cell} title="Growth of $1 · OOS stitched" info={INFO.walk_forward} skeletonHeight={380}>
            {(d) => <WfEquity d={d} />}
          </Panel>
          <Panel<WalkForwardOut> {...cell} title="SR · IS vs OOS · by fold" info={INFO.wfe} skeletonHeight={260}>
            {(d) => <FoldSharpes d={d} />}
          </Panel>
          <Panel<WalkForwardOut> {...cell} title="Folds" flush skeletonHeight={240}>
            {(d) => <FoldTable d={d} />}
          </Panel>
        </>
      )}
    </div>
  );
}

function WfVerdict({ d }: { d: WalkForwardOut }) {
  const v = walkForwardVerdict(d);
  const oos = d.oos_summary;
  const ref = d.reference;
  return (
    <VerdictBlock
      value={v.value}
      detail={v.detail}
      figs={[
        { k: "OOS SR", v: fmtSR(d.oos_sharpe), sub: `MEAN IS ${fmtSR(d.mean_is_sharpe)} · ${d.folds.length} FOLDS` },
        { k: "WFE", v: finite(d.walk_forward_efficiency) ? fmtPct(d.walk_forward_efficiency, 0) : "—", tone: finite(d.walk_forward_efficiency) && d.walk_forward_efficiency < 0 ? "loss" : "", sub: "OOS SR ÷ MEAN IS SR" },
        { k: "OOS return", v: fmtPct(oos.total_return, 1, { signed: true }), tone: finite(oos.total_return) && oos.total_return <= 0 ? "loss" : "", sub: `CAGR ${fmtPct(oos.cagr, 1, { signed: true })}` },
        { k: "Fixed params SR", v: ref ? fmtSR(ref.summary.sharpe) : "—", sub: ref ? paramLine(ref.params) : "YOURS NOT IN GRID" },
        { k: "Distinct picks", v: `${d.distinct_choices} / ${d.combos.length}`, sub: `BENCH SR ${fmtSR(d.benchmark_summary.sharpe)}` },
      ]}
      gates={v.gates}
    >
      <Readline items={[{ k: "OOS", v: `${fmtDate(d.folds[0]?.oos_start)} – ${fmtDate(d.folds.at(-1)?.oos_end)}` }, { k: "MAX DD", v: fmtPct(oos.max_drawdown, 1), tone: "loss" }]} />
    </VerdictBlock>
  );
}

function WfEquity({ d }: { d: WalkForwardOut }) {
  const [log, setLog] = useState(true);
  const series = useMemo<XYSeries[]>(
    () => [
      { name: "WALK-FWD", y: d.equity.data.walk_forward, tone: "ink", width: 2 },
      ...(d.equity.data.base_params ? [{ name: "FIXED", y: d.equity.data.base_params, tone: "ink2" as const, dash: "dot" as const }] : []),
      { name: "BENCH", y: d.equity.data.benchmark, tone: "ink3" },
    ],
    [d],
  );
  return (
    <>
      <div className="sl-chart-tools">
        <Toggle label="LOG" checked={log} onChange={setLog} />
      </div>
      <XYChart time x={d.equity.index as string[]} series={series} yFormat="usd" digits={2} logY={log} hlines={[{ at: 1, label: "$1", tone: "ink3" }]} height={360} ariaLabel="Growth of one dollar from the stitched out-of-sample blocks, against fixed parameters and the benchmark" />
    </>
  );
}

function FoldSharpes({ d }: { d: WalkForwardOut }) {
  const x = d.folds.map((_, i) => i + 1);
  return (
    <XYChart
      x={x}
      series={[
        { name: "IS", y: d.folds.map((f) => f.is_sharpe), tone: "ink3", dash: "dash" },
        { name: "OOS", y: d.folds.map((f) => f.oos_sharpe), tone: "ink", width: 1.5 },
        { name: "OOS < 0", y: d.folds.map((f) => (finite(f.oos_sharpe) && f.oos_sharpe < 0 ? f.oos_sharpe : null)), mode: "points", tone: "signal", size: 5, label: false },
      ]}
      hlines={[{ at: 0, label: "0", tone: "ink3" }]}
      xFormat="int"
      xTitle="Fold"
      digits={2}
      height={250}
      ariaLabel="In-sample Sharpe of each fold's pick against its out-of-sample Sharpe"
    />
  );
}

function FoldTable({ d }: { d: WalkForwardOut }) {
  const cols: Column<Fold & { i: number }>[] = [
    { key: "i", label: "Fold", render: (f) => <span className="num">F{f.i + 1}</span> },
    { key: "params", label: "Pick", sortable: false, render: (f) => <span className="num">{paramLine(f.params)}</span> },
    { key: "is_start", label: "In-sample", hideBelow: 900, render: (f) => <span className="num sl-gate-dim">{`${fmtDate(f.is_start, "month")} – ${fmtDate(f.is_end, "month")}`}</span> },
    { key: "is_sharpe", label: "IS SR", numeric: true, format: (v) => fmtSR(v) },
    { key: "oos_start", label: "Traded", hideBelow: 600, render: (f) => <span className="num">{`${fmtDate(f.oos_start, "month")} – ${fmtDate(f.oos_end, "month")}`}</span> },
    { key: "oos_sharpe", label: "OOS SR", numeric: true, format: (v) => fmtSR(v), color: "sign" },
    { key: "oos_return", label: "OOS ret", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign" },
    { key: "switch_cost", label: "Switch", numeric: true, format: (v) => (v ? fmtPct(v, 2) : "—"), hideBelow: 1200, info: { title: "Switch cost", text: "Cost of moving from the previous fold's weights to the new pick's weights at the switch." } },
  ];
  return <DataTable columns={cols} rows={d.folds.map((f, i) => ({ ...f, i }))} rowKey={(f) => f.i} compact />;
}

