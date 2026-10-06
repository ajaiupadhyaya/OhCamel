/**
 * /risk?alpha=0.99&h=10 (the command line's PORT RISK link) → the levels the Risk controls
 * offer. Off-grid requests snap to the nearest offered level; garbage falls back to 99% / 1D.
 */
export type SeedAlpha = "0.95" | "0.99";
export type SeedHorizon = "1" | "10";

const ALPHAS = [0.95, 0.99] as const;
const HORIZONS = [1, 10] as const;

function nearest<T extends number>(v: number, grid: readonly T[]): T {
  return grid.reduce((best, g) => (Math.abs(g - v) < Math.abs(best - v) ? g : best), grid[0]);
}

export function riskSeed(params: URLSearchParams): { alpha: SeedAlpha; h: SeedHorizon } {
  let a = Number(params.get("alpha"));
  if (Number.isFinite(a) && a > 1 && a < 100) a /= 100;
  const alpha: SeedAlpha = Number.isFinite(a) && a > 0.5 && a < 1 ? (String(nearest(a, ALPHAS)) as SeedAlpha) : "0.99";
  const hv = Number(params.get("h"));
  const h: SeedHorizon = Number.isFinite(hv) && hv >= 1 && hv <= 250 ? (String(nearest(hv, HORIZONS)) as SeedHorizon) : "1";
  return { alpha, h };
}
