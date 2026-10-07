/**
 * BACKTEST · 1/N: walk-forward, out-of-sample comparison of allocation methods against 1/N
 * after trading costs, led by the verdict (PASS when a method beats 1/N at 5% on the
 * Ledoit–Wolf HAC test after a Holm correction across the methods tested, FAIL when none does), then growth, the Sharpe differences with their
 * intervals, drawdowns and the scorecard. POST /portfolio/compare runs on RUN.
 */
import { useEffect, useMemo, useState } from "react";
import { DataTable, NumberField, Panel, SegmentedControl, Toggle, type Column } from "../../components";
import { CoefChart } from "../../charts/CoefChart";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { Note, Verdict } from "../../design";
import { fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import { COMPARE_DEFAULT, compareBody, shallowEqualJson, type CompareDraft } from "./config";
import { beatVerdict, sharpeVerdicts } from "./derive";
import { COV_CODE, INFO, METHOD_CODE } from "./info";
import { QPanel, Readline, sym, useOpt } from "./shared";
import type { CompareOut, CompareStats, MethodName } from "./types";

const MAX_METHODS = 8; // 1/N always included
const ALL: MethodName[] = ["equal_weight", "inverse_volatility", "min_variance", "max_sharpe", "mean_variance", "risk_parity", "hrp", "herc", "max_diversification", "min_cvar"];

export function CompareTab({ enabled }: { enabled: boolean }) {
  const { cfg } = useOpt();
  const [draft, setDraft] = useState<CompareDraft>(COMPARE_DEFAULT);
  const live = useMemo(() => compareBody(cfg, draft), [cfg, draft]);
  const [runBody, setRunBody] = useState<ReturnType<typeof compareBody> | null>(null);
  useEffect(() => {
    if (enabled && !runBody && cfg.tickers.length >= 2) setRunBody(live);
  }, [enabled, runBody, live, cfg.tickers.length]);
  const q = useApiPost<CompareOut>("/portfolio/compare", runBody, { enabled: enabled && !!runBody });
  const stale = !!runBody && !shallowEqualJson(runBody, live);
  const upd = (p: Partial<CompareDraft>) => setDraft((d) => ({ ...d, ...p }));
  const toggle = (m: MethodName) => {
    if (m === "equal_weight") return;
    const has = draft.methods.includes(m);
    if (!has && draft.methods.length >= MAX_METHODS) return;
    upd({ methods: has ? draft.methods.filter((x) => x !== m) : ALL.filter((x) => x === m || draft.methods.includes(x)) });
  };
  const running = q.isFetching;
  const asOf = q.data ? String(q.data.equity.index[q.data.equity.index.length - 1] ?? "") : undefined;

  return (
    <div className="op-tab-body">
      <Panel
        title={
          <>
            Walk-forward · setup
            <Note n={4} to="walkforward-opt" />
          </>
        }
        actions={
          <button type="button" className={`btn btn-sm ${!runBody || stale ? "btn-primary" : ""}`} disabled={running || cfg.tickers.length < 2 || (!!runBody && !stale && !q.isError)} onClick={() => setRunBody(live)}>
            {running ? "RUNNING" : !runBody || stale || q.isError ? "RUN" : "CURRENT"}
          </button>
        }
      >
        <div className="op-cmp-controls">
          <div className="oc-field">
            <span className="oc-field-label">
              Methods <span className="num">· {draft.methods.length}/{MAX_METHODS} · 1/N BENCH</span>
            </span>
            <div className="op-toggle-chips" role="group" aria-label="Methods to compare">
              {ALL.map((m) => {
                const on = draft.methods.includes(m);
                const blocked = !on && draft.methods.length >= MAX_METHODS;
                return (
                  <button key={m} type="button" aria-pressed={on} disabled={m === "equal_weight" || blocked} className={`op-tchip num ${on ? "on" : ""}`} onClick={() => toggle(m)} title={m === "mean_variance" ? `Uses the method's mean–variance target (${cfg.mvTarget.replace("_", " ")})` : undefined}>
                    {METHOD_CODE[m]}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="op-cmp-row">
            <div className="oc-field">
              <span className="oc-field-label">Est window</span>
              <SegmentedControl size="sm" ariaLabel="Estimation window" options={[{ value: "252", label: "1Y" }, { value: "504", label: "2Y" }, { value: "756", label: "3Y" }]} value={String(draft.window)} onChange={(v) => upd({ window: Number(v) })} />
            </div>
            <div className="oc-field">
              <span className="oc-field-label">Rebalance</span>
              <SegmentedControl size="sm" ariaLabel="Rebalance frequency" options={[{ value: "W", label: "W" }, { value: "M", label: "M" }, { value: "Q", label: "Q" }]} value={draft.rebalance} onChange={(rebalance) => upd({ rebalance })} />
            </div>
            <div className="oc-field">
              <span className="oc-field-label">{sym("μ")}</span>
              <SegmentedControl size="sm" ariaLabel="Mean estimator" options={[{ value: "historical", label: "HIST" }, { value: "james_stein", label: "J–S" }]} value={draft.muMethod} onChange={(muMethod) => upd({ muMethod })} />
            </div>
            <NumberField label="Cost" ariaLabel="Trading cost, basis points one way" value={draft.costBps} unit="bp" min={0} max={500} step={1} width={100} onChange={(costBps) => upd({ costBps })} />
          </div>
          <Readline
            items={[
              { k: "Σ", v: COV_CODE[cfg.cov] },
              { k: "RF", v: cfg.rfMode === "manual" ? `${fmtPct(cfg.rf, 2)} · MANUAL` : "3M BILL" },
              { k: "W", v: `${fmtPct(cfg.longOnly ? Math.max(0, cfg.minWeight) : cfg.minWeight, 0)} … ${fmtPct(cfg.maxWeight, 0)}` },
              { k: "BURN-IN", v: `${draft.window}D` },
              stale && { k: "INPUTS", v: "CHANGED", tone: "loss" },
            ]}
          />
        </div>
      </Panel>

      <QPanel<CompareOut> q={q} title="Vs 1/N · OOS" info={INFO.sharpeTest} asOf={asOf} skeletonHeight={160} notes={[]} provenance={[]} empty={!runBody}>
        {(d) => <VerdictBlock d={d} />}
      </QPanel>

      {!q.isError && runBody && (
        <>
          <QPanel<CompareOut> q={q} title="Growth of $1 · OOS · net" asOf={asOf} skeletonHeight={380} notes={[]} provenance={[]}>
            {(d) => <EquityChart d={d} />}
          </QPanel>

          <div className="grid-2">
            <QPanel<CompareOut> q={q} title="ΔSR vs 1/N · 95% CI" info={INFO.sharpeTest} asOf={asOf} skeletonHeight={300} notes={[]} provenance={[]}>
              {(d) => <Forest d={d} />}
            </QPanel>
            <QPanel<CompareOut> q={q} title="Drawdown" info="max_drawdown" asOf={asOf} skeletonHeight={300} notes={[]} provenance={[]}>
              {(d) => <DrawdownChart d={d} />}
            </QPanel>
          </div>

          <QPanel<CompareOut> q={q} title="Scorecard · OOS · net" asOf={asOf} flush skeletonHeight={300}>
            {(d) => <ScoreTable d={d} />}
          </QPanel>
        </>
      )}
    </div>
  );
}

export function VerdictBlock({ d }: { d: CompareOut }) {
  const v = sharpeVerdicts(d);
  const b = beatVerdict(v);
  const bench = d.stats.find((s) => s.method === d.method.benchmark);
  const best = [...d.stats].sort((a, c) => (c.sharpe ?? -Infinity) - (a.sharpe ?? -Infinity))[0];
  const winners = v.filter((x) => x.better);
  const detail = b.value === "INSUFFICIENT DATA" ? "NO TESTABLE METHOD" : `${b.better} OF ${b.n} BEAT 1/N · LW HAC 5% · HOLM ACROSS ${b.n} TESTS`;
  return (
    <div className="op-verdict">
      <Verdict value={b.value} detail={detail} />
      <div className="op-verdict-grid num">
        <div>
          <span className="op-verdict-k">OOS</span>
          <span>
            {fmtDate(d.equity.index[0] as string)} – {fmtDate(d.equity.index[d.equity.index.length - 1] as string)}
          </span>
          <span className="op-verdict-sub">
            {d.rebalance_dates.length} REBAL · {d.method.window}D WINDOW · {fmtNum(d.method.cost_bps, 0)}BP
          </span>
        </div>
        <div>
          <span className="op-verdict-k">1/N SR</span>
          <span className="op-big">{fmtNum(bench?.sharpe, 2)}</span>
          <span className="op-verdict-sub">
            CAGR {fmtPct(bench?.cagr, 1, { signed: true })} · VOL {fmtPct(bench?.annual_vol, 1)}
          </span>
        </div>
        <div>
          <span className="op-verdict-k">TOP SR</span>
          <span className="op-big">{fmtNum(best?.sharpe, 2)}</span>
          <span className="op-verdict-sub">{best ? METHOD_CODE[best.method] : "—"}</span>
        </div>
        <div>
          <span className="op-verdict-k">BETTER · SAME · WORSE</span>
          <span className="op-big">
            {b.better} · {b.same} · <span className={b.worse > 0 ? "loss" : ""}>{b.worse}</span>
          </span>
          <span className="op-verdict-sub">{v.filter((x) => (x.diff ?? 0) > 0).length} HIGHER POINT ESTIMATE</span>
        </div>
      </div>
      {winners.length > 0 && (
        <Readline items={winners.map((w) => ({ k: METHOD_CODE[w.method], v: `ΔSR ${fmtNum(w.diff, 2, { signed: true })} · p ${fmtNum(w.p, 3)} · HOLM ${fmtNum(w.pHolm, 3)}` }))} />
      )}
    </div>
  );
}

/** 1/N heavy ink; every other method thin ink-2, named at its line end. */
function curves(f: CompareOut["equity"], d: CompareOut, benchTone: "ink" | "signal", rest: "ink2" | "ink3"): XYSeries[] {
  return f.columns.map((c) => ({ name: c === d.method.benchmark ? "1/N" : METHOD_CODE[c as MethodName] ?? (d.method.labels[c] ?? c).toUpperCase(), y: f.data[c], tone: c === d.method.benchmark ? benchTone : rest, width: c === d.method.benchmark ? 2 : 1 }));
}

function EquityChart({ d }: { d: CompareOut }) {
  const [log, setLog] = useState(false);
  const series = useMemo(() => curves(d.equity, d, "ink", "ink2"), [d]);
  return (
    <>
      <div className="op-chart-tools">
        <Toggle label="LOG" checked={log} onChange={setLog} />
      </div>
      <XYChart time x={d.equity.index as string[]} series={series} yFormat="usd" digits={2} logY={log} hlines={[{ at: 1, label: "$1", tone: "ink3" }]} height={380} ariaLabel="Growth of one dollar out of sample, after costs" />
    </>
  );
}

function DrawdownChart({ d }: { d: CompareOut }) {
  const series = useMemo(() => curves(d.drawdown, d, "signal", "ink3"), [d]);
  return <XYChart time x={d.drawdown.index as string[]} series={series} yFormat="pct" digits={1} height={300} ariaLabel="Drawdown from previous peak by method" />;
}

function Forest({ d }: { d: CompareOut }) {
  const rows = useMemo(
    () =>
      sharpeVerdicts(d)
        .filter((x) => x.diff != null)
        .sort((a, b) => (b.diff ?? 0) - (a.diff ?? 0))
        .map((x) => ({ term: METHOD_CODE[x.method], est: x.diff as number, lo: (x.diff as number) - 1.96 * (x.se ?? 0), hi: (x.diff as number) + 1.96 * (x.se ?? 0), t: x.se ? (x.diff as number) / x.se : 0, p: x.p, ph: x.pHolm })),
    [d],
  );
  return <CoefChart rows={rows} sigT={1.96} tick={(v) => fmtNum(v, 1, { signed: true })} right={(r) => `p ${fmtNum((r as (typeof rows)[number]).p, 3)} · HOLM ${fmtNum((r as (typeof rows)[number]).ph, 3)}`} ariaLabel="Annual Sharpe difference against 1/N with 95% intervals" />;
}

function ScoreTable({ d }: { d: CompareOut }) {
  type Row = CompareStats & { diff: number | null; p: number | null; ph: number | null; worse: boolean; pm: number | null };
  const rows = useMemo<Row[]>(() => {
    const v = new Map(sharpeVerdicts(d).map((x) => [x.method, x]));
    return d.stats.map((s) => {
      const t = d.tests[s.method];
      const x = v.get(s.method);
      return { ...s, diff: t?.ledoit_wolf?.sharpe_diff_annual ?? null, p: t?.ledoit_wolf?.p_value ?? null, ph: x?.pHolm ?? null, worse: x?.worse ?? false, pm: t?.memmel?.p_value ?? null };
    });
  }, [d]);
  const cols: Column<Row>[] = [
    {
      key: "label",
      label: "Method",
      render: (r) => (
        <span className={`num ${r.method === d.method.benchmark ? "op-strong" : ""}`} title={(r.failed_rebalances ?? 0) > 0 ? "Rebalances where the optimiser failed and the previous weights were kept" : undefined}>
          {r.method === d.method.benchmark ? "1/N · BENCH" : METHOD_CODE[r.method]}
          {(r.failed_rebalances ?? 0) > 0 && <span className="loss"> · {r.failed_rebalances} FAILED</span>}
        </span>
      ),
    },
    { key: "cagr", label: "CAGR", numeric: true, format: (v) => fmtPct(v, 1, { signed: true }), color: "sign", info: "cagr" },
    { key: "annual_vol", label: "Vol", numeric: true, format: (v) => fmtPct(v, 1), info: "vol" },
    { key: "sharpe", label: "SR", numeric: true, format: (v) => fmtNum(v, 2), info: "sharpe" },
    { key: "sortino", label: "Sortino", numeric: true, format: (v) => fmtNum(v, 2), info: "sortino", hideBelow: 1200 },
    { key: "max_drawdown", label: "Max DD", numeric: true, format: (v) => fmtPct(v, 1), info: "max_drawdown", color: () => "loss" },
    { key: "calmar", label: "Calmar", numeric: true, format: (v) => fmtNum(v, 2), info: INFO.calmar, hideBelow: 1200 },
    { key: "annual_turnover", label: "TO / yr", numeric: true, format: (v) => fmtPct(v, 0), info: INFO.annualTurnover, hideBelow: 600 },
    { key: "cost_drag_annual", label: "Cost drag", numeric: true, format: (v) => fmtPct(v, 2), info: INFO.costDrag, hideBelow: 900 },
    { key: "diff", label: "ΔSR", numeric: true, format: (v) => fmtNum(v, 2, { signed: true }), info: INFO.sharpeTest },
    { key: "p", label: "p LW", numeric: true, format: (v) => fmtNum(v, 3), info: { title: "p-value, Ledoit–Wolf", text: "Probability of a Sharpe gap this large if the method and 1/N were equally good. Raw, one test at a time.", reference: "Ledoit & Wolf (2008), J. Empirical Finance 15(5)" } },
    { key: "ph", label: "p Holm", numeric: true, format: (v) => fmtNum(v, 3), color: (_v, r) => (r.worse ? "loss" : undefined), info: { title: "p-value, Holm-adjusted", text: "The Ledoit–Wolf p corrected for testing every method against 1/N at once. The verdict uses this. Significantly worse is marked in signal.", reference: "Holm (1979), Scand. J. Statistics 6(2)" } },
    { key: "pm", label: "p JK-M", numeric: true, format: (v) => fmtNum(v, 3), hideBelow: 1200, info: { title: "p-value, Jobson–Korkie–Memmel", text: "The classic Sharpe-difference test (normal, independent returns): a cross-check on the robust one.", reference: "Jobson & Korkie (1981); Memmel (2003)" } },
  ];
  return <DataTable<Row> columns={cols} rows={rows} rowKey={(r) => r.method} defaultSort={{ key: "sharpe", dir: "desc" }} isActive={(r) => r.method === d.method.benchmark} />;
}
