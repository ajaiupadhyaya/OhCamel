/** Headline cards for the broad indices (+ VIX level when available). */
import { Link } from "react-router-dom";
import { Sparkline } from "../../components";
import { InfoTip } from "../../components/InfoTip";
import { fmtNum, fmtSignedPct, signClass } from "../../lib/format";
import type { OverviewRow, PeriodKey } from "./data";

export function IndexCard({ row, period, periodLabel, level }: { row: OverviewRow; period: PeriodKey; periodLabel: string; level?: boolean }) {
  const r = row[period] as number | null | undefined;
  if (row.error) {
    return (
      <div className="mk-card mk-card-off" title={row.error}>
        <div className="mk-card-head">
          <span className="num mk-card-ticker">{row.ticker}</span>
          <span className="mk-card-name">{row.name}</span>
        </div>
        <div className="mk-card-unavail">Unavailable</div>
        <div className="mk-card-reason">{row.error.replace(/^[^:]+:\s*/, "")}</div>
      </div>
    );
  }
  return (
    <Link to={`/ticker/${encodeURIComponent(row.ticker)}`} className="mk-card">
      <div className="mk-card-head">
        <span className="num mk-card-ticker">{row.ticker}</span>
        <span className="mk-card-name">{row.name}</span>
        {level && <InfoTip info="vix" size={12} />}
      </div>
      <div className="mk-card-body">
        <div>
          <div className="mk-card-last num">{fmtNum(row.last, 2)}</div>
          <div className={`mk-card-chg num ${signClass(r)}`}>
            {fmtSignedPct(r)} <span className="subtle">{periodLabel}</span>
          </div>
        </div>
        <Sparkline values={row.sparkline?.values ?? []} width={84} height={38} color={level ? "var(--lilac)" : undefined} title={`${row.ticker} 6-month trend`} />
      </div>
    </Link>
  );
}
