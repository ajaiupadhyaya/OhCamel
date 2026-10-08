/**
 * Verdicts and readings derived from the Strategy Lab's responses and the products' tables.
 *
 * The lab's verdicts apply docs/CHARTER.md's gates literally to what one interactive request
 * can measure, and say NOT RUN for the rest. A single backtest, a sweep or a walk-forward is
 * never the full battery, so the best the lab can say is ADVISORY; any measured gate that
 * fails makes it FAIL. The farm and the models carry their own verdicts from Lane M: those
 * are read, never recomputed here.
 */
import type { VerdictValue } from "../../design";
import { fmtMultiple, fmtNum, fmtPct } from "../../lib/format";
import type { CostsOut, FarmBoardRow, FarmCellRow, RunOut, SweepOut, WalkForwardOut } from "./types";

/** docs/CHARTER.md "The gates" (and quant/src/ohcamel_quant/models/verdict.py). */
export const CHARTER = {
  dsr: 0.3,
  psr: 0.7,
  /** Bootstrap: lower 5th percentile of the Sharpe > 0, i.e. P(SR <= 0) < 5% across resamples. */
  bootP: 0.05,
  regimes: 3,
  regimesOf: 5,
  pboHigh: 0.5,
  costs: [0, 5, 15, 30],
} as const;

export type GateStatus = "PASS" | "FAIL" | "NOT RUN" | "REPORTED" | "N/A";

export interface LabGate {
  code: string;
  value: string;
  rule: string;
  status: GateStatus;
}

export interface LabVerdict {
  value: VerdictValue;
  detail: string;
  gates: LabGate[];
}

const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const pass = (ok: boolean): GateStatus => (ok ? "PASS" : "FAIL");

function psrGate(psr: number | null | undefined): LabGate & { fail?: string } {
  if (!finite(psr)) return { code: "PSR", value: "—", rule: `≥ ${fmtNum(CHARTER.psr, 2)}`, status: "N/A" };
  const ok = psr >= CHARTER.psr;
  return { code: "PSR", value: fmtNum(psr, 2), rule: `≥ ${fmtNum(CHARTER.psr, 2)}`, status: pass(ok), fail: ok ? undefined : `PSR ${fmtNum(psr, 2)} < ${fmtNum(CHARTER.psr, 2)}` };
}

const notRun = (code: string, where: string): LabGate => ({ code, value: where, rule: "—", status: "NOT RUN" });

function settle(gates: (LabGate & { fail?: string })[], measured: boolean, advisory: string): LabVerdict {
  const fails = gates.map((g) => g.fail).filter((f): f is string => !!f);
  const clean = gates.map(({ code, value, rule, status }) => ({ code, value, rule, status }));
  if (fails.length) return { value: "FAIL", detail: fails.join(" · "), gates: clean };
  if (!measured) return { value: "INSUFFICIENT DATA", detail: "NOTHING MEASURABLE", gates: clean };
  return { value: "ADVISORY", detail: advisory, gates: clean };
}

/** POST /backtest/run: PSR and the bootstrap are measured; DSR, holdout and costs are not. */
export function backtestVerdict(r: RunOut): LabVerdict {
  const psr = psrGate(r.psr?.psr_vs_0);
  const p = r.bootstrap_sharpe?.prob_sharpe_le_0;
  const boot: LabGate & { fail?: string } = finite(p)
    ? { code: "BOOT LO 5%", value: `P(SR≤0) ${fmtPct(p, 1)}`, rule: `P(SR≤0) < ${fmtPct(CHARTER.bootP, 0)}`, status: pass(p < CHARTER.bootP), fail: p < CHARTER.bootP ? undefined : `P(SR≤0) ${fmtPct(p, 1)} ≥ ${fmtPct(CHARTER.bootP, 0)}` }
    : { code: "BOOT LO 5%", value: "—", rule: `P(SR≤0) < ${fmtPct(CHARTER.bootP, 0)}`, status: "N/A" };
  const audit = r.look_ahead_audit;
  const look: LabGate & { fail?: string } = { code: "LOOK-AHEAD", value: audit ? `${audit.points} RE-RUNS` : "—", rule: "SAME DECISIONS", status: audit ? pass(audit.passed) : "N/A", fail: audit && !audit.passed ? "LOOK-AHEAD AUDIT FAILED" : undefined };
  const gates = [notRun("HOLDOUT", "WALK-FWD"), notRun("DSR", "SWEEP"), psr, boot, notRun("PBO", "SWEEP"), notRun("REGIMES", "FARM"), notRun("COSTS", "COSTS"), look];
  return settle(gates, finite(r.psr?.psr_vs_0) || finite(p), "SINGLE RUN · DSR · HOLDOUT · COSTS NOT RUN");
}

/** POST /backtest/sweep: DSR deflated by every combination tried, PSR of the pick, PBO, SPA. */
export function sweepVerdict(d: SweepOut): LabVerdict {
  const s = d.deflated_sharpe;
  const dsr: LabGate & { fail?: string } = finite(s?.dsr)
    ? { code: "DSR", value: `${fmtNum(s.dsr, 2)} · N ${s.n_trials}`, rule: `≥ ${fmtNum(CHARTER.dsr, 2)}`, status: pass(s.dsr >= CHARTER.dsr), fail: s.dsr >= CHARTER.dsr ? undefined : `DSR ${fmtNum(s.dsr, 2)} < ${fmtNum(CHARTER.dsr, 2)}` }
    : { code: "DSR", value: "—", rule: `≥ ${fmtNum(CHARTER.dsr, 2)}`, status: "N/A" };
  const pbo = d.pbo?.pbo;
  const high = finite(pbo) && pbo > CHARTER.pboHigh;
  const pboGate: LabGate = { code: "PBO", value: finite(pbo) ? `${fmtNum(pbo, 2)}${high ? " HIGH" : ""}` : "—", rule: "REPORTED", status: finite(pbo) ? "REPORTED" : "N/A" };
  const spa = d.spa?.pvalue_consistent;
  const spaGate: LabGate = { code: "SPA", value: finite(spa) ? `p ${fmtNum(spa, 3)}` : "—", rule: "REPORTED", status: finite(spa) ? "REPORTED" : "N/A" };
  const v = settle([notRun("HOLDOUT", "WALK-FWD"), dsr, psrGate(s?.psr_vs_0), pboGate, spaGate, notRun("REGIMES", "FARM"), notRun("COSTS", "COSTS")], finite(s?.dsr), "IN-SAMPLE · HOLDOUT NOT RUN");
  if (high && v.value !== "INSUFFICIENT DATA") v.detail += ` · PBO ${fmtNum(pbo, 2)} HIGH`;
  if (!finite(s?.dsr)) return { ...v, value: "INSUFFICIENT DATA", detail: "NO DSR" };
  return v;
}

/** POST /backtest/walkforward: the charter's holdout gate on the stitched out-of-sample record. */
export function walkForwardVerdict(d: WalkForwardOut): LabVerdict {
  const ret = d.oos_summary?.total_return;
  const hold: LabGate & { fail?: string } = finite(ret)
    ? { code: "HOLDOUT", value: fmtPct(ret, 1, { signed: true }), rule: "> 0", status: pass(ret > 0), fail: ret > 0 ? undefined : `HOLDOUT ${fmtPct(ret, 1)} ≤ 0` }
    : { code: "HOLDOUT", value: "—", rule: "> 0", status: "N/A" };
  const wfe = d.walk_forward_efficiency;
  const v = settle([hold, notRun("DSR", "FARM"), psrGate(d.oos_summary?.psr_vs_0), notRun("REGIMES", "FARM"), notRun("COSTS", "COSTS")], finite(ret), `WFE ${finite(wfe) ? fmtPct(wfe, 0) : "—"} · DSR · REGIMES NOT RUN`);
  if (!finite(ret)) return { ...v, value: "INSUFFICIENT DATA", detail: "NO OUT-OF-SAMPLE RETURN" };
  return v;
}

/** POST /backtest/costs: the Sharpe at the user's own cost, the break-even, and the charter grid. */
export function costsVerdict(d: CostsOut, yourBps: number | null): LabVerdict {
  const curve = d.curve ?? [];
  const levels = new Set(curve.map((c) => c.cost_bps));
  const missing = CHARTER.costs.filter((c) => !levels.has(c));
  const grid: LabGate = { code: "COSTS", value: missing.length ? `MISSING ${missing.join(" · ")}` : CHARTER.costs.join(" / ") + " BP", rule: "0/5/15/30 REPORTED", status: missing.length ? "NOT RUN" : "REPORTED" };
  const at = curve.find((c) => c.cost_bps === yourBps);
  const be = d.breakeven_bps_sharpe_zero;
  const beText = finite(be) ? `BREAK-EVEN ${fmtNum(be, be < 10 ? 1 : 0)} BP` : "NO BREAK-EVEN";
  if (!at || !finite(at.sharpe) || !finite(yourBps)) return { value: "INSUFFICIENT DATA", detail: curve.length ? "YOUR COST NOT ON THE LADDER" : "NO LADDER", gates: [grid] };
  const srGate: LabGate = { code: "SR @ YOUR COST", value: fmtNum(at.sharpe, 2), rule: "> 0", status: pass(at.sharpe > 0) };
  if (at.sharpe <= 0) return { value: "FAIL", detail: `SR ${fmtNum(at.sharpe, 2)} AT ${fmtNum(yourBps, 0)} BP · ${beText}`, gates: [srGate, grid] };
  const margin = finite(be) && yourBps > 0 ? ` · ${fmtMultiple(be / yourBps, 1)} YOUR ${fmtNum(yourBps, 0)} BP` : "";
  return { value: "ADVISORY", detail: `${beText}${margin}`, gates: [srGate, grid] };
}

// ------------------------------------------------------------------ farm (farm.sweep)

/** Short codes for the charter gate names models/verdict.py writes. */
export const GATE_CODE: Record<string, string> = {
  holdout_positive: "HOLD",
  dsr: "DSR",
  psr: "PSR",
  bootstrap_lower_5pct: "BOOT",
  regimes_positive: "REG",
  pbo: "PBO",
  cost_sweep: "COST",
  beats_linear_composite: "VS LIN",
};

/** The gate names a charter verdict's detail lists as failed ("failed: dsr 0.12 (needs >= 0.3), ..."). */
export function failedGates(detail: string | null | undefined): string[] {
  if (!detail || !/^failed:/.test(detail)) return [];
  return [...detail.matchAll(/(\w+) (?:[-−]?[\d.]+|n\/a) \(needs/g)].map((m) => m[1]);
}

/** True when the verdict named the PBO as high ("; PBO 0.71 (high)"). */
export function pboHigh(detail: string | null | undefined): boolean {
  return !!detail && /PBO [\d.]+ \(high\)/.test(detail);
}

export function farmCounts(board: FarmBoardRow[], cells: FarmCellRow[]) {
  const count = (v: string) => board.filter((r) => r.verdict === v).length;
  return { fail: count("FAIL"), pass: count("PASS"), insufficient: count("INSUFFICIENT DATA"), skipped: cells.filter((c) => c.status === "skipped").length, total: cells.length };
}

export interface CostRow {
  cost_bps: number;
  sharpe: number | null;
  cagr: number | null;
}

/** A farm cell's cost curve (JSON of backtest.validation.cost_sensitivity's curve). */
export function costRows(json: string | null | undefined): CostRow[] {
  if (!json) return [];
  try {
    const v = JSON.parse(json) as unknown;
    if (!Array.isArray(v)) return [];
    return v
      .filter((r): r is Record<string, unknown> => !!r && typeof r === "object" && finite((r as Record<string, unknown>).cost_bps))
      .map((r) => ({ cost_bps: r.cost_bps as number, sharpe: finite(r.sharpe) ? r.sharpe : null, cagr: finite(r.cagr) ? r.cagr : null }));
  } catch {
    return [];
  }
}

/** "{\"lookback\": 252}" → "LOOKBACK 252"; anything unparseable is shown as it came. */
export function paramsText(json: string | null | undefined): string {
  if (!json) return "—";
  try {
    const v = JSON.parse(json) as unknown;
    if (!v || typeof v !== "object") return String(json);
    const parts = Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k.replace(/_/g, " ").toUpperCase()} ${typeof x === "number" ? fmtNum(x, Number.isInteger(x) ? 0 : 2) : String(x)}`);
    return parts.length ? parts.join(" · ") : "DEFAULTS";
  } catch {
    return String(json);
  }
}

// ------------------------------------------------------------------ models (models.xs_lgbm)

/** The date of a holdout_returns row, whatever the serialized frame named its index column. */
export function holdoutDates(r: Record<string, unknown>): string | null {
  for (const k of ["date", "index", "level_0"]) if (typeof r[k] === "string") return r[k] as string;
  return null;
}

/** Growth of $1 from net returns, model and the linear composite. */
export function growthCurve(rows: Record<string, unknown>[]) {
  const x: string[] = [];
  const model: number[] = [];
  const composite: (number | null)[] = [];
  let a = 1;
  let b = 1;
  let bOk = true;
  for (const r of rows) {
    const d = holdoutDates(r);
    if (!d) continue;
    x.push(d);
    if (finite(r.model_net)) a *= 1 + r.model_net;
    model.push(a);
    if (finite(r.composite_net)) b *= 1 + r.composite_net;
    else bOk = false;
    composite.push(bOk ? b : null);
  }
  return { x, model, composite };
}
