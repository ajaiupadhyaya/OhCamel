/**
 * INDICES: one Figure per headline instrument, linking to its ticker page. Ticker in Archivo
 * caps, the close in Plex Mono, the period change signed (gains ink, losses --signal; a level
 * such as the VIX keeps its change in ink), a 6-month trace. A failed read says UNAVAILABLE.
 */
import { Link } from "react-router-dom";
import { Sparkline } from "../../components";
import { fmtNum, fmtSignedPct, signClass } from "../../lib/format";
import type { OverviewRow, PeriodKey } from "./data";

export function IndexFigure({ row, period, periodLabel, level }: { row: OverviewRow; period: PeriodKey; periodLabel: string; level?: boolean }) {
  const r = row[period] as number | null | undefined;
  if (row.error) {
    return (
      <div className="oc-stat mk-fig mk-fig-off" title={row.error}>
        <div className="oc-stat-label">{row.ticker}</div>
        <div className="mk-fig-value num">—</div>
        <div className="mk-fig-foot num">UNAVAILABLE</div>
        <div className="mk-fig-name">{row.name}</div>
      </div>
    );
  }
  return (
    <Link to={`/ticker/${encodeURIComponent(row.ticker)}`} className="oc-stat mk-fig">
      <div className="oc-stat-label">{row.ticker}</div>
      <div className="mk-fig-value num">{fmtNum(row.last, 2)}</div>
      <div className="mk-fig-foot num">
        <span className={level ? "" : signClass(r)}>{fmtSignedPct(r)}</span> {periodLabel}
      </div>
      <div className="mk-fig-name">{row.name}</div>
      <Sparkline className="mk-fig-spark" values={row.sparkline?.values ?? []} width={120} height={24} area={false} strokeWidth={1.25} color={level ? "var(--ink)" : undefined} />
    </Link>
  );
}
