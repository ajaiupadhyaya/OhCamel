/** The execution frontier as a chart wants it: risk (std, dollars) ascending on x, expected cost on y. */
import type { ExecutionFrontierRow } from "../portfolio/types";

export interface FrontierXY {
  x: number[];
  y: number[];
}

/** Rows with a finite std and cost only, sorted by risk: a frontier drawn from unsorted rows would zig-zag. */
export function frontierXY(rows: ExecutionFrontierRow[]): FrontierXY {
  const ok = rows.filter((r) => Number.isFinite(r.std) && Number.isFinite(r.expected_cost)).sort((a, b) => a.std - b.std);
  return { x: ok.map((r) => r.std), y: ok.map((r) => r.expected_cost) };
}

/** The sentence beside the square-root number: the schedule's cost is impact only, and ε is not in it. */
export const EXECUTION_CAVEAT =
  "η and γ are assumptions, not estimates; σ is the realised 20-day volatility. Costs are impact only: the fixed half-spread (ε) is not in them.";
