/**
 * SWEEP (POST /api/backtest/sweep): the rule over a parameter grid, then the overfitting
 * battery. Verdict first: the DSR deflated by every combination tried against the charter's
 * 0.30 and the pick's PSR against 0.70 (FAIL), else ADVISORY: a sweep is in-sample, the
 * holdout is the walk-forward's. Then the Sharpe surface, every combination's equity, the
 * CSCV logit distribution (PBO), the degradation scatter, DSR against the luck bar, SPA and
 * the full table.
 */
import { useEffect, useMemo, useState } from "react";
import { DataTable, Field, Panel, SegmentedControl, type Column } from "../../components";
import { HeatmapChart } from "../../charts/UPlot";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { fmtDate, fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import { humanize, sweepableParams, type BaseBody } from "./config";
import { sweepVerdict, CHARTER } from "./derive";
import { GridEditor, defaultAxes, gridFromAxes, type Axis } from "./GridEditor";
import { INFO } from "./info";
import { Readline, VerdictBlock, finite, fmtProb, fmtSR, paramLabel, paramLine } from "./shared";
import type { StrategySpec, SweepOut } from "./types";

export function SweepTab({ spec, base, maxCombos, onData }: { spec: StrategySpec; base: BaseBody | null; maxCombos: number; onData?: (d: SweepOut | undefined) => void }) {
  const params = useMemo(() => sweepableParams(spec), [spec]);
  const [axes, setAxes] = useState<Axis[]>(() => defaultAxes(params, 2));
  const [partitions, setPartitions] = useState("16");
  const draft = gridFromAxes(axes, params);
  const [submitted, setSubmitted] = useState(() => ({ grid: draft.grid, n_partitions: 16 }));
  const body = base && params.length && Object.keys(submitted.grid).length ? { ...base, ...submitted } : null;
  const q = useApiPost<SweepOut>("/backtest/sweep", body, { enabled: !!body });
  useEffect(() => onData?.(q.data), [q.data, onData]);

  if (!params.length)
    return (
      <div className="sl-tab">
        <div className="sl-empty">
          <Absent reason="NO NUMERIC PARAMETERS · NOTHING TO SWEEP" source={spec.key} />
        </div>
      </div>
    );

  const tooMany = draft.combos > maxCombos;
  const changed = JSON.stringify({ grid: draft.grid, n_partitions: Number(partitions) }) !== JSON.stringify(submitted);
  const run = () => setSubmitted({ grid: draft.grid, n_partitions: Number(partitions) });
  const asOf = q.data?.window.end;
  const cell = { query: q, asOf, notes: [] as string[] };

  return (
    <div className="sl-tab">
      <Panel
        title={
          <>
            Sweep · grid
            <Note n={4} to="pbo" />
          </>
        }
        actions={
          <button type="button" className={`btn btn-sm ${changed || q.isError ? "btn-primary" : ""}`} disabled={!!draft.error || tooMany || !base || q.isFetching || (!changed && !q.isError)} onClick={run}>
            {q.isFetching ? "RUNNING" : changed || q.isError ? "RUN" : "CURRENT"}
          </button>
        }
      >
        <div className="sl-controls">
          <GridEditor axes={axes} onChange={setAxes} params={params} />
          <div className="sl-controls-row">
            <Field label="CSCV blocks · S">
              <SegmentedControl size="sm" ariaLabel="CSCV partitions" options={["8", "10", "12", "16"]} value={partitions} onChange={setPartitions} />
            </Field>
          </div>
          <Readline
            items={[
              { k: "COMBOS", v: draft.error ? "—" : `${draft.combos} / ${maxCombos}`, tone: tooMany ? "loss" : "" },
              draft.error ? { k: "INPUT", v: draft.error, tone: "loss" } : null,
              tooMany && { k: "CAP", v: `MAX ${maxCombos}`, tone: "loss" },
              changed && !draft.error && { k: "INPUTS", v: "CHANGED", tone: "loss" },
            ]}
          />
        </div>
      </Panel>

      <Panel<SweepOut> {...cell} notes={undefined} title="Verdict · sweep" skeletonHeight={300}>
        {(d) => <SweepVerdict d={d} />}
      </Panel>

      {!q.isError && q.data && (
        <>
          <div className="grid-2">
            <Panel<SweepOut> {...cell} title="SR · grid · net" info={INFO.sharpe} skeletonHeight={340}>
              {(d) => <SharpeGrid d={d} base={base} />}
            </Panel>
            <Panel<SweepOut> {...cell} title="Growth of $1 · every combo" skeletonHeight={340}>
              {(d) => <EquityFan d={d} />}
            </Panel>
          </div>
          <div className="grid-2">
            <Panel<SweepOut> {...cell} title="PBO · CSCV logits" info={INFO.logit} skeletonHeight={300}>
              {(d) => <Logits d={d} />}
            </Panel>
            <Panel<SweepOut> {...cell} title="Degradation · IS → OOS SR" info={INFO.degradation} skeletonHeight={300}>
              {(d) => <Degradation d={d} />}
            </Panel>
          </div>
          <div className="grid-2">
            <Panel<SweepOut>
              {...cell}
              title={
                <>
                  DSR · luck bar
                  <Note n={5} to="dsr" />
                </>
              }
              info={INFO.dsr}
              skeletonHeight={240}
            >
              {(d) => <Dsr d={d} />}
            </Panel>
            <Panel<SweepOut>
              {...cell}
              title={
                <>
                  SPA · vs buy-and-hold
                  <Note n={6} to="bootstrap-spa" />
                </>
              }
              info={INFO.spa}
              skeletonHeight={240}
            >
              {(d) => <Spa d={d} />}
            </Panel>
          </div>
          <Panel<SweepOut> {...cell} title="All combinations" flush skeletonHeight={260}>
            {(d) => <ComboTable d={d} />}
          </Panel>
        </>
      )}
    </div>
  );
}

function SweepVerdict({ d }: { d: SweepOut }) {
  const v = sweepVerdict(d);
  const s = d.deflated_sharpe;
  return (
    <VerdictBlock
      value={v.value}
      detail={v.detail}
      figs={[
        { k: "PBO", v: finite(d.pbo.pbo) ? fmtNum(d.pbo.pbo, 2) : "—", tone: finite(d.pbo.pbo) && d.pbo.pbo > CHARTER.pboHigh ? "loss" : "", sub: d.pbo.n_combinations ? `${fmtNum(d.pbo.n_combinations, 0)} CSCV SPLITS · S ${d.pbo.n_partitions}` : (d.pbo.error ?? "").toUpperCase() },
        { k: "DSR", v: fmtNum(s.dsr, 2), tone: finite(s.dsr) && s.dsr < CHARTER.dsr ? "loss" : "", sub: `N ${s.n_trials} · PSR ${fmtNum(s.psr_vs_0, 2)}` },
        { k: "Best SR", v: fmtSR(d.best.sharpe), sub: paramLine(d.best.params) },
        { k: "Luck · SR0", v: fmtSR(s.sr0_annualized), sub: `E[MAX] OF ${s.n_trials} NULL TRIALS` },
        { k: "SPA p", v: fmtNum(d.spa.pvalue_consistent, 3), sub: `H0 · NONE BEATS ${d.benchmark.ticker}` },
      ]}
      gates={v.gates}
    >
      <Readline items={[{ k: "WINDOW", v: `${fmtDate(d.window.start)} – ${fmtDate(d.window.end)}` }, { k: "SESSIONS", v: fmtNum(d.window.sessions, 0) }, { k: "COMBOS", v: d.combos.length }]} />
    </VerdictBlock>
  );
}

function SharpeGrid({ d, base }: { d: SweepOut; base: BaseBody | null }) {
  const h = d.heatmap;
  const cur = (k: string) => (base?.params?.[k] as number | undefined) ?? (d.base_params[k] as number | undefined);
  if (!h.y_param || !h.y_values) {
    const z = h.z as (number | null)[];
    const best = d.best.params[h.x_param];
    const c = cur(h.x_param);
    return (
      <XYChart
        x={h.x_values}
        series={[{ name: "SR", y: z, mode: "bars", tone: "ink2" }]}
        vlines={[{ at: best, label: "BEST", tone: "ink" }, ...(finite(c) && c !== best ? [{ at: c, label: "YOURS", tone: "ink2" as const, dash: "dot" as const }] : [])]}
        hlines={[{ at: 0, label: "0", tone: "ink3" }]}
        xTitle={humanize(h.x_param)}
        digits={2}
        height={320}
        ariaLabel={`Net Sharpe ratio across ${h.x_param}`}
      />
    );
  }
  const z = h.z as (number | null)[][];
  const bx = h.x_values.indexOf(d.best.params[h.x_param]);
  const by = h.y_values.indexOf(d.best.params[h.y_param]);
  const cx = h.x_values.indexOf(Number(cur(h.x_param)));
  const cy = h.y_values.indexOf(Number(cur(h.y_param)));
  const rect = (x: number, y: number, dash: string) => ({ type: "rect", xref: "x", yref: "y", x0: x - 0.5, x1: x + 0.5, y0: y - 0.5, y1: y + 0.5, line: { color: "var(--ink)", width: 2, dash } });
  const shapes = [...(bx >= 0 && by >= 0 ? [rect(bx, by, "solid")] : []), ...(cx >= 0 && cy >= 0 && (cx !== bx || cy !== by) ? [rect(cx, cy, "dot")] : [])];
  return (
    <>
      <HeatmapChart
        x={h.x_values.map(paramLabel)}
        y={h.y_values.map(paramLabel)}
        z={z}
        format="num"
        digits={2}
        diverging
        showValues={h.x_values.length * h.y_values.length <= 64}
        height={Math.max(260, h.y_values.length * 40 + 70)}
        layout={{ xaxis: { title: { text: humanize(h.x_param) } }, yaxis: { title: { text: humanize(h.y_param) }, autorange: true }, shapes } as never}
      />
      <Readline items={[{ k: "SOLID", v: "BEST IN-SAMPLE" }, { k: "DOTTED", v: "YOURS" }]} />
    </>
  );
}

function EquityFan({ d }: { d: SweepOut }) {
  const f = d.equity_weekly;
  const best = `#${d.best.combo}`;
  // The fan as its envelope (min, median, max across combinations each week) and the pick.
  const series = useMemo<XYSeries[]>(() => {
    const cols = f.columns.map((c) => f.data[c]);
    const at = (i: number) => cols.map((c) => c[i]).filter(finite).sort((a, b) => a - b);
    const q = (xs: number[], p: number) => (xs.length ? xs[Math.min(xs.length - 1, Math.floor(p * (xs.length - 1) + 0.5))] : null);
    const n = f.index.length;
    const env = Array.from({ length: n }, (_, i) => at(i));
    return [
      { name: "MAX", y: env.map((xs) => q(xs, 1)), tone: "ink3" },
      { name: "MEDIAN", y: env.map((xs) => q(xs, 0.5)), tone: "ink2", dash: "dot" },
      { name: "MIN", y: env.map((xs) => q(xs, 0)), tone: "ink3" },
      { name: "BEST IS", y: f.data[best], tone: "ink", width: 2 },
    ];
  }, [f, best]);
  return <XYChart time x={f.index as string[]} series={series} yFormat="usd" digits={2} logY hlines={[{ at: 1, label: "$1", tone: "ink3" }]} height={320} ariaLabel={`Growth of one dollar across ${f.columns.length} parameter combinations: envelope, median and the in-sample best`} />;
}

function Logits({ d }: { d: SweepOut }) {
  const p = d.pbo;
  const data = useMemo(() => {
    const l = p.logits;
    if (!l) return null;
    const total = l.counts.reduce((a, c) => a + c, 0) || 1;
    const x = l.counts.map((_, i) => (l.edges[i] + l.edges[i + 1]) / 2);
    const share = l.counts.map((c) => c / total);
    return { x, below: share.map((s, i) => (x[i] <= 0 ? s : null)), above: share.map((s, i) => (x[i] > 0 ? s : null)) };
  }, [p.logits]);
  if (p.error || !data) return <Absent reason={(p.error ?? "NO LOGITS").toUpperCase()} source="cscv_pbo" />;
  return (
    <>
      <XYChart
        x={data.x}
        series={[
          { name: "λ ≤ 0", y: data.below, mode: "bars", tone: "signal" },
          { name: "λ > 0", y: data.above, mode: "bars", tone: "ink2" },
        ]}
        vlines={[{ at: 0, label: `PBO ${fmtNum(p.pbo, 2)}`, tone: "ink" }]}
        yFormat="pct"
        digits={1}
        zero
        xTitle="λ · logit OOS rank"
        height={250}
        ariaLabel="Distribution of the logit of the in-sample winner's out-of-sample rank across CSCV splits; mass at or below zero is the PBO"
      />
      <Readline
        items={[
          { k: "MEDIAN λ", v: fmtNum(p.logits?.median, 2) },
          { k: "MEAN λ", v: fmtNum(p.logits?.mean, 2) },
          { k: "S × SPLITS", v: `${p.n_partitions} × ${fmtNum(p.n_combinations, 0)}` },
          { k: "SESSIONS", v: fmtNum(p.rows_used, 0) },
        ]}
      />
    </>
  );
}

function Degradation({ d }: { d: SweepOut }) {
  const g = d.pbo.degradation;
  const data = useMemo(() => {
    if (!g) return null;
    const pts = g.is_sharpe_ann.map((x, i) => [x, g.oos_sharpe_ann[i]] as const).filter(([x, y]) => finite(x) && finite(y)).sort((a, b) => a[0] - b[0]);
    const k = Math.sqrt(252);
    const fit = finite(g.slope) && finite(g.intercept) ? pts.map(([x]) => (g.intercept as number) * k + (g.slope as number) * x) : null;
    return {
      x: pts.map((p) => p[0]),
      gain: pts.map(([, y]) => (y >= 0 ? y : null)),
      loss: pts.map(([, y]) => (y < 0 ? y : null)),
      diag: pts.map(([x]) => x),
      fit,
    };
  }, [g]);
  if (!g || !data) return <Absent reason="NO DEGRADATION FIT" source="cscv_pbo" />;
  const series: XYSeries[] = [
    { name: "OOS ≥ 0", y: data.gain, mode: "points", tone: "ink3", size: 3 },
    { name: "OOS < 0", y: data.loss, mode: "points", tone: "signal", size: 3 },
    { name: "IS = OOS", y: data.diag, tone: "ink3", dash: "dash" },
    ...(data.fit ? [{ name: "FIT", y: data.fit, tone: "ink" as const, width: 1.5 }] : []),
  ];
  return (
    <>
      <XYChart x={data.x} series={series} xTitle="SR · in-sample" digits={2} height={250} ariaLabel="In-sample Sharpe of the chosen parameters against their out-of-sample Sharpe, one point per CSCV split" />
      <Readline
        items={[
          { k: "SLOPE", v: fmtNum(g.slope, 2), tone: finite(g.slope) && g.slope < 0 ? "loss" : "" },
          { k: "p", v: fmtNum(g.slope_pvalue, 3) },
          { k: "R²", v: fmtNum(g.r2, 2) },
          { k: "P(OOS SR < 0)", v: fmtProb(g.prob_oos_loss) },
        ]}
      />
    </>
  );
}

function Dsr({ d }: { d: SweepOut }) {
  const s = d.deflated_sharpe;
  if (s.error) return <Absent reason={s.error.toUpperCase()} source="deflated_sharpe" />;
  const rows: [string, string, string?][] = [
    ["SR · SELECTED", fmtSR(s.sharpe_ann)],
    ["SR0 · LUCK", fmtSR(s.sr0_annualized)],
    ["TRIALS · N", fmtNum(s.n_trials, 0)],
    ["V[SR] · PER PERIOD", fmtNum(s.var_sr, 6)],
    ["PSR · SR > 0", fmtNum(s.psr_vs_0, 2)],
    ["DSR · SR > SR0", fmtNum(s.dsr, 2), finite(s.dsr) && s.dsr < CHARTER.dsr ? "loss" : ""],
    ["CHARTER BAR", `≥ ${fmtNum(CHARTER.dsr, 2)}`],
  ];
  return <Kv rows={rows} />;
}

function Spa({ d }: { d: SweepOut }) {
  const s = d.spa;
  if (s.error) return <Absent reason={s.error.toUpperCase()} source="spa_test" />;
  const rows: [string, string, string?][] = [
    ["p · CONSISTENT", fmtNum(s.pvalue_consistent, 3)],
    ["p · LOWER", fmtNum(s.pvalue_lower, 3)],
    ["p · UPPER · RC", fmtNum(s.pvalue_upper, 3)],
    ["MODELS", fmtNum(s.n_models, 0)],
    ["BLOCK", `${fmtNum(s.block_size, 0)}D`],
    ["REPS", fmtNum(s.reps, 0)],
    ["BENCH", `${d.benchmark.ticker} · SR ${fmtSR(d.benchmark.summary.sharpe)}`],
  ];
  return <Kv rows={rows} />;
}

export function Kv({ rows }: { rows: [string, string, string?][] }) {
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

function ComboTable({ d }: { d: SweepOut }) {
  const keys = Object.keys(d.grid);
  type Row = SweepOut["table"][number];
  const cols: Column<Row>[] = [
    ...keys.map((k) => ({ key: k, label: humanize(k), numeric: true, format: (v: number) => paramLabel(v) })),
    { key: "sharpe", label: "SR", numeric: true, format: (v: number) => fmtSR(v), info: INFO.sharpe },
    { key: "sortino", label: "Sortino", numeric: true, format: (v: number) => fmtSR(v), hideBelow: 900 },
    { key: "cagr", label: "CAGR", numeric: true, format: (v: number) => fmtPct(v, 1, { signed: true }), color: "sign" },
    { key: "ann_vol", label: "Vol", numeric: true, format: (v: number) => fmtPct(v, 1), hideBelow: 600 },
    { key: "max_drawdown", label: "Max DD", numeric: true, format: (v: number) => fmtPct(v, 1), color: () => "loss", hideBelow: 600 },
    { key: "annual_turnover", label: "TO / Y", numeric: true, format: (v: number) => fmtMultiple(v, 1), hideBelow: 900 },
    {
      key: "picked",
      label: "CSCV pick",
      numeric: true,
      value: (r) => d.pbo.selected_counts?.[r.combo] ?? null,
      format: (v: number | null) => (v == null || !d.pbo.n_combinations ? "—" : fmtPct(v / d.pbo.n_combinations, 1)),
      info: { title: "Picked in CSCV", text: "Share of the CSCV splits in which this combination was the in-sample winner." },
    },
  ];
  return <DataTable<Row> columns={cols} rows={d.table} rowKey={(r) => r.combo} defaultSort={{ key: "sharpe", dir: "desc" }} isActive={(r) => r.combo === d.best.combo} compact />;
}
