/**
 * Tile heatmap of sector returns for the chosen period. Colour intensity is the return
 * relative to a period-appropriate scale (±2% for 1D … ±30% for 1Y) on the diverging
 * loss↔gain ramp; text always stays in ink colours for legibility.
 */
import { useNavigate } from "react-router-dom";
import { fmtPct, fmtSignedPct } from "../../lib/format";
import type { OverviewRow, PeriodKey } from "./data";

export function tileColor(v: number | null | undefined, scale: number): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "var(--surface-2)";
  const t = Math.max(-1, Math.min(1, v / scale));
  const pct = Math.round(8 + Math.abs(t) * 52);
  return `color-mix(in srgb, var(${t >= 0 ? "--gain" : "--loss"}) ${pct}%, var(--surface))`;
}

export function SectorHeatmap({ rows, period, scale }: { rows: OverviewRow[]; period: PeriodKey; scale: number }) {
  const nav = useNavigate();
  const sorted = [...rows].sort((a, b) => {
    if (a.error && !b.error) return 1;
    if (b.error && !a.error) return -1;
    return ((b[period] as number) ?? -Infinity) - ((a[period] as number) ?? -Infinity);
  });
  return (
    <div>
      <div className="mk-heat" role="list">
        {sorted.map((r) => {
          const v = r[period] as number | null | undefined;
          const strong = v !== null && v !== undefined && Math.abs(v / scale) > 0.6;
          return (
            <button
              key={r.ticker}
              role="listitem"
              className={`mk-tile ${r.error ? "mk-tile-off" : ""} ${strong ? "mk-tile-strong" : ""}`}
              style={{ background: r.error ? undefined : tileColor(v, scale) }}
              onClick={() => !r.error && nav(`/ticker/${encodeURIComponent(r.ticker)}`)}
              disabled={!!r.error}
              title={r.error ?? `${r.name} (${r.ticker}): ${fmtSignedPct(v)}`}
            >
              <span className="mk-tile-name">{r.name}</span>
              <span className="mk-tile-ticker num">{r.ticker}</span>
              <span className="mk-tile-value num">{r.error ? "no data" : fmtSignedPct(v)}</span>
            </button>
          );
        })}
      </div>
      <div className="mk-legend" aria-hidden>
        <span className="num">{fmtPct(-scale, 0)}</span>
        <span className="mk-legend-ramp" />
        <span className="num">+{fmtPct(scale, 0)}</span>
      </div>
    </div>
  );
}
