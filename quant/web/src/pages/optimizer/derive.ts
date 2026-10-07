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
  /** Raw two-sided p of the Sharpe-difference test. */
  p: number | null;
  /** Holm–Bonferroni adjusted p across every method with a testable result. */
  pHolm: number | null;
  se: number | null;
  /** Significant at 5% after Holm, with the sign. */
  better: boolean;
  worse: boolean;
}

/** The family-wise level the verdict is held to. */
export const ALPHA = 0.05;

/**
 * Holm–Bonferroni step-down adjusted p-values (Holm 1979), in input order.
 * Sorted ascending, p_(i) becomes max over j <= i of min(1, (n - j + 1) p_(j)).
 */
export function holmAdjust(ps: number[]): number[] {
  const n = ps.length;
  const order = ps.map((p, i) => [p, i] as const).sort((a, b) => a[0] - b[0]);
  const out = new Array<number>(n);
  let run = 0;
  order.forEach(([p, i], k) => {
    run = Math.max(run, Math.min(1, (n - k) * p));
    out[i] = run;
  });
  return out;
}

/**
 * One row per method tested against the benchmark (Ledoit–Wolf HAC, else Memmel).
 * Better/worse are called on Holm-adjusted p across the n tested methods (CHARTER
 * principle 4: no uncorrected signal scans), so one lucky method out of seven cannot PASS.
 */
export function sharpeVerdicts(d: CompareOut): SharpeVerdict[] {
  const rows = d.stats
    .filter((s) => d.tests[s.method])
    .map((s) => {
      const t = d.tests[s.method].ledoit_wolf ?? d.tests[s.method].memmel;
      return { method: s.method, label: s.label, diff: t?.sharpe_diff_annual ?? null, p: t?.p_value ?? null, se: t?.se_annual ?? null };
    });
  const testable = rows.filter((r) => r.diff != null && r.p != null);
  const adj = holmAdjust(testable.map((r) => r.p as number));
  const holm = new Map(testable.map((r, i) => [r, adj[i]]));
  return rows.map((r) => {
    const pHolm = holm.get(r) ?? null;
    const sig = pHolm != null && pHolm < ALPHA;
    return { ...r, pHolm, better: sig && (r.diff as number) > 0, worse: sig && (r.diff as number) < 0 };
  });
}

/** PASS when at least one method beats 1/N at 5% after Holm across the n tests; FAIL when none does. */
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
