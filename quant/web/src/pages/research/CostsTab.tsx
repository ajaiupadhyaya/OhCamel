/**
 * COSTS (POST /api/backtest/costs): the same positions re-priced at other one-way costs
 * (turnover does not depend on cost), with the break-even costs. Verdict first: FAIL when
 * the Sharpe is not positive at the cost the user set; the charter's 0/5/15/30 bps ladder
 * is REPORTED when every level is on it.
 */
import { useState } from "react";
import { DataTable, Field, Panel, type Column } from "../../components";
import { XYChart, type XYRule } from "../../charts/XYChart";
import { Note } from "../../design";
import { fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import { useApiPost } from "../../lib/query";
import type { BaseBody } from "./config";
import { CHARTER, costsVerdict } from "./derive";
import { INFO } from "./info";
import { Readline, VerdictBlock, finite, fmtSR } from "./shared";
import type { CostPoint, CostsOut } from "./types";

const DEFAULT_BPS = [0, 1, 2, 5, 10, 15, 20, 30, 50, 75, 100];

function parseBps(text: string, cap: number): { values: number[]; error: string | null } {
  const vals: number[] = [];
  for (const p of text.split(/[\s,;]+/).filter(Boolean)) {
    const v = Number(p);
    if (!Number.isFinite(v)) return { values: [], error: `NOT A NUMBER · ${p}` };
    if (v < 0 || v > 1000) return { values: [], error: "0 … 1000 BP" };
    vals.push(v);
  }
  const u = [...new Set(vals)].sort((a, b) => a - b);
  if (u.length < 2) return { values: u, error: "MIN 2 LEVELS" };
  if (u.length > cap) return { values: u, error: `MAX ${cap} LEVELS` };
  return { values: u, error: null };
}

export function CostsTab({ base, cap }: { base: BaseBody | null; cap: number }) {
  const [text, setText] = useState(() => [...new Set([...DEFAULT_BPS, base?.cost_bps ?? 5])].sort((a, b) => a - b).join(", "));
  const parsed = parseBps(text, cap);
  const [submitted, setSubmitted] = useState<number[]>(parsed.values);
  const body = base ? { ...base, bps: submitted } : null;
  const q = useApiPost<CostsOut>("/backtest/costs", body, { enabled: !!body && submitted.length >= 2 });
  const changed = parsed.values.join() !== submitted.join();
  const yours = base?.cost_bps ?? null;
  const cell = { query: q, asOf: q.data?.window.end, notes: [] as string[] };

  return (
    <div className="sl-tab">
      <Panel
        title={
          <>
            Cost ladder · setup
            <Note n={4} to="wfo-costs" />
          </>
        }
        actions={
          <button type="button" className={`btn btn-sm ${changed || q.isError ? "btn-primary" : ""}`} disabled={!!parsed.error || !base || q.isFetching || (!changed && !q.isError)} onClick={() => setSubmitted(parsed.values)}>
            {q.isFetching ? "RUNNING" : changed || q.isError ? "RUN" : "CURRENT"}
          </button>
        }
      >
        <div className="sl-controls">
          <Field label="Levels · bp one-way" info={INFO.cost_bps}>
            <input className="input num sl-values sl-values-wide" value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Cost levels in basis points" />
          </Field>
          <Readline
            items={[
              { k: "LEVELS", v: `${parsed.values.length} / ${cap}` },
              parsed.error ? { k: "INPUT", v: parsed.error, tone: "loss" } : null,
              { k: "YOURS", v: finite(yours) ? `${fmtNum(yours, 0)} BP` : "—" },
              { k: "CHARTER", v: CHARTER.costs.join(" / ") + " BP" },
              changed && !parsed.error && { k: "INPUTS", v: "CHANGED", tone: "loss" },
            ]}
          />
        </div>
      </Panel>

      <Panel<CostsOut> {...cell} notes={undefined} title="Verdict · costs" skeletonHeight={220}>
        {(d) => <CostsVerdict d={d} yourBps={yours} />}
      </Panel>

      {!q.isError && q.data && (
        <>
          <div className="grid-2">
            <Panel<CostsOut> {...cell} title="SR vs cost" info={INFO.breakeven} skeletonHeight={300}>
              {(d) => <CostCurve d={d} metric="sharpe" yourBps={yours} />}
            </Panel>
            <Panel<CostsOut> {...cell} title="CAGR vs cost" info={INFO.cagr} skeletonHeight={300}>
              {(d) => <CostCurve d={d} metric="cagr" yourBps={yours} />}
            </Panel>
          </div>
          <Panel<CostsOut> {...cell} title="Cost ladder" flush skeletonHeight={240}>
            {(d) => <CostTable d={d} yourBps={yours} />}
          </Panel>
        </>
      )}
    </div>
  );
}

function CostsVerdict({ d, yourBps }: { d: CostsOut; yourBps: number | null }) {
  const v = costsVerdict(d, yourBps);
  const be = d.breakeven_bps_sharpe_zero;
  const bb = d.breakeven_bps_vs_benchmark;
  return (
    <VerdictBlock
      value={v.value}
      detail={v.detail}
      figs={[
        { k: "Break-even · SR 0", v: finite(be) ? `${fmtNum(be, be < 10 ? 1 : 0)}BP` : "—", sub: finite(be) ? "ONE-WAY" : "NOT CROSSED" },
        { k: `Break-even · ${d.benchmark.ticker}`, v: finite(bb) ? `${fmtNum(bb, bb < 10 ? 1 : 0)}BP` : "—", sub: "CAGR = BENCH CAGR" },
        { k: "Turnover", v: fmtMultiple(d.annual_turnover, 1), sub: `${fmtPct((d.annual_turnover ?? 0) / 10000, 2)}/Y PER BP` },
        { k: "Margin", v: finite(be) && finite(yourBps) && yourBps > 0 ? fmtMultiple(be / yourBps, 1) : "—", sub: finite(yourBps) ? `BREAK-EVEN ÷ ${fmtNum(yourBps, 0)}BP` : "" },
      ]}
      gates={v.gates}
    />
  );
}

function CostCurve({ d, metric, yourBps }: { d: CostsOut; metric: "sharpe" | "cagr"; yourBps: number | null }) {
  const bench = d.benchmark.summary[metric];
  const be = metric === "sharpe" ? d.breakeven_bps_sharpe_zero : d.breakeven_bps_vs_benchmark;
  const xmax = Math.max(...d.curve.map((c) => c.cost_bps));
  const hlines: XYRule[] = [...(metric === "sharpe" ? [{ at: 0, label: "0", tone: "ink3" as const }] : []), ...(finite(bench) ? [{ at: bench, label: d.benchmark.ticker, tone: "ink2" as const }] : [])];
  const vlines: XYRule[] = [...(finite(be) && be <= xmax * 1.02 ? [{ at: be, label: `BE ${fmtNum(be, be < 10 ? 1 : 0)}`, tone: "signal" as const, dash: "solid" as const }] : []), ...(finite(yourBps) ? [{ at: yourBps, label: "YOURS", tone: "ink2" as const, dash: "dot" as const }] : [])];
  return (
    <XYChart
      x={d.curve.map((c) => c.cost_bps)}
      series={[{ name: metric === "sharpe" ? "SR" : "CAGR", y: d.curve.map((c) => c[metric]), tone: "ink", width: 1.5 }]}
      hlines={hlines}
      vlines={vlines}
      yFormat={metric === "sharpe" ? "num" : "pct"}
      digits={metric === "sharpe" ? 2 : 1}
      xFormat="int"
      xTitle="Cost · bp"
      height={280}
      ariaLabel={`Net ${metric === "sharpe" ? "Sharpe ratio" : "CAGR"} at each one-way cost level`}
    />
  );
}

function CostTable({ d, yourBps }: { d: CostsOut; yourBps: number | null }) {
  const cols: Column<CostPoint>[] = [
    { key: "cost_bps", label: "Cost", numeric: true, format: (v) => `${fmtNum(v, 0)}BP` },
    { key: "sharpe", label: "SR", numeric: true, format: (v) => fmtSR(v), color: "sign", info: INFO.sharpe },
    { key: "cagr", label: "CAGR", numeric: true, format: (v) => fmtPct(v, 2, { signed: true }), color: "sign" },
    { key: "annual_cost_drag", label: "Drag / Y", numeric: true, format: (v) => fmtPct(v, 2), info: INFO.cost_drag },
    { key: "ann_vol", label: "Vol", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 600 },
    { key: "max_drawdown", label: "Max DD", numeric: true, format: (v) => fmtPct(v, 1), color: () => "loss", hideBelow: 600 },
  ];
  return <DataTable columns={cols} rows={d.curve} rowKey={(c) => c.cost_bps} isActive={(c) => c.cost_bps === yourBps} compact />;
}
