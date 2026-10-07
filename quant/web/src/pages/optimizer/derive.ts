/**
 * Pure derivations for the Optimizer page: the walk-forward verdict against 1/N, frontier
 * slices for the weights-along-the-frontier table, and the Black–Litterman views read back.
 */
import type { VerdictValue } from "../../design";
import type { BLView, CompareOut, FrontierPoint, MethodName, OptimizeOut } from "./types";

export interface SharpeVerdict {
  method: MethodName;
  label: string;
  diff: number | null;
  p: number | null;
  se: number | null;
  better: boolean;
  worse: boolean;
}

/** One row per method tested against the benchmark (Ledoit–Wolf HAC, else Memmel). */
export function sharpeVerdicts(d: CompareOut): SharpeVerdict[] {
  return d.stats
    .filter((s) => d.tests[s.method])
    .map((s) => {
      const t = d.tests[s.method].ledoit_wolf ?? d.tests[s.method].memmel;
      const diff = t?.sharpe_diff_annual ?? null;
      const p = t?.p_value ?? null;
      const sig = diff != null && p != null && p < 0.05;
      return { method: s.method, label: s.label, diff, p, se: t?.se_annual ?? null, better: sig && diff > 0, worse: sig && diff < 0 };
    });
}

/** PASS when at least one method beats 1/N at 5% (unadjusted); FAIL when none does. */
export function beatVerdict(v: SharpeVerdict[]): { value: VerdictValue; better: number; worse: number; same: number; n: number } {
  const tested = v.filter((x) => x.diff != null && x.p != null);
  const better = tested.filter((x) => x.better).length;
  const worse = tested.filter((x) => x.worse).length;
  const n = tested.length;
  const value: VerdictValue = n === 0 ? "INSUFFICIENT DATA" : better > 0 ? "PASS" : "FAIL";
  return { value, better, worse, same: n - better - worse, n };
}

/** k frontier portfolios evenly spaced in volatility (nearest point to each target), ends included. */
export function frontierSlices(pts: FrontierPoint[], k = 7): FrontierPoint[] {
  if (pts.length <= k) return pts;
  const sorted = [...pts].sort((a, b) => a.vol - b.vol);
  const lo = sorted[0].vol;
  const hi = sorted[sorted.length - 1].vol;
  const out: FrontierPoint[] = [];
  for (let i = 0; i < k; i++) {
    const target = lo + ((hi - lo) * i) / (k - 1);
    const best = sorted.reduce((b, p) => (Math.abs(p.vol - target) < Math.abs(b.vol - target) ? p : b), sorted[0]);
    if (!out.includes(best)) out.push(best);
  }
  return out;
}

/** A weight to six decimals, for the portfolio store. */
export function roundWeight(w: number): number {
  const r = Math.round(w * 1e6) / 1e6;
  return r === 0 ? 0 : r;
}

export type ViewRow = BLView & { posterior: number | null; pull: number | null };

/** Each view: where the posterior settled (long − short, or excess + rf) and the share of the prior→view gap it travelled. */
export function viewRows(e: OptimizeOut["expected_returns"]): ViewRow[] {
  const rf = Number(e.params.risk_free ?? 0);
  const post = e.posterior_excess;
  return (e.views ?? []).map((v) => {
    const posterior = post ? (v.short ? (post[v.long] ?? NaN) - (post[v.short] ?? NaN) : (post[v.long] ?? NaN) + rf) : null;
    const gap = v.value - v.prior_implied;
    return { ...v, posterior, pull: posterior != null && Math.abs(gap) > 1e-9 ? (posterior - v.prior_implied) / gap : null };
  });
}
