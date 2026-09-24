/** The per-universe table: returns across horizons, vol, 52-week range and a 6-month sparkline. */
import { useNavigate } from "react-router-dom";
import { DataTable, Sparkline, type Column } from "../../components";
import { EM_DASH, fmtNum, fmtPct, fmtSignedPct } from "../../lib/format";
import type { OverviewRow } from "./data";

function RangeBar({ row }: { row: OverviewRow }) {
  const { low_52w: lo, high_52w: hi, last } = row;
  if (lo == null || hi == null || last == null || hi <= lo) return <span className="subtle">{EM_DASH}</span>;
  const pos = Math.max(0, Math.min(1, (last - lo) / (hi - lo)));
  return (
    <span className="mk-range" title={`52-week low ${fmtNum(lo)} · high ${fmtNum(hi)} · last ${fmtNum(last)}`}>
      <span className="mk-range-track" />
      <span className="mk-range-dot" style={{ left: `${pos * 100}%` }} />
    </span>
  );
}

const ret = (key: keyof OverviewRow, label: string, hideBelow?: number, info?: string): Column<OverviewRow> => ({
  key: key as string,
  label,
  numeric: true,
  format: (v) => fmtSignedPct(v, key === "ret_1d" || key === "ret_1w" ? 2 : 1),
  color: "sign",
  hideBelow,
  info,
});

export function MarketTable({ rows }: { rows: OverviewRow[] }) {
  const nav = useNavigate();
  const columns: Column<OverviewRow>[] = [
    {
      key: "ticker",
      label: "Instrument",
      render: (r) => (
        <span className="mk-inst">
          <span className="num mk-inst-ticker">{r.ticker}</span>
          <span className="mk-inst-name">{r.name !== r.ticker ? r.name : ""}</span>
          {r.error && (
            <span className="badge unknown" title={r.error}>
              unavailable
            </span>
          )}
        </span>
      ),
    },
    { key: "last", label: "Last", numeric: true, format: (v) => fmtNum(v, 2) },
    ret("ret_1d", "1D", undefined, "return_1d"),
    ret("ret_1w", "1W", 600),
    ret("ret_1m", "1M", 600),
    ret("ret_3m", "3M", 900),
    ret("ret_ytd", "YTD", 600, "ytd"),
    ret("ret_1y", "1Y", 900),
    { key: "vol_1m_ann", label: "Vol 1M", numeric: true, format: (v) => fmtPct(v, 1), hideBelow: 1200, info: { title: "1-month realized volatility", text: "Standard deviation of daily log returns over the last calendar month, annualized. A 20% vol means a typical ±20% swing over a year.", formula: "\\sigma = \\sqrt{N}\\,\\operatorname{sd}(\\ln P_t - \\ln P_{t-1})" } },
    { key: "range", label: "52W range", value: (r) => r.dist_52w_high, render: (r) => <RangeBar row={r} />, align: "center", hideBelow: 1200, info: { title: "52-week range", text: "Where the last close sits between the lowest low and highest high of the past year (erroneous prints clipped)." } },
    { key: "spark", label: "6M", sortable: false, value: (r) => r.sparkline?.values?.length ?? 0, render: (r) => <Sparkline values={r.sparkline?.values ?? []} width={76} height={22} area={false} />, align: "right", hideBelow: 600 },
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.ticker}
      onRowClick={(r) => !r.error && nav(`/ticker/${encodeURIComponent(r.ticker)}`)}
    />
  );
}
