/**
 * The tape: one 24px line under the masthead scrolling the index, rates, credit and
 * commodity tape from GET /api/market/overview. Pauses on hover; static under
 * prefers-reduced-motion. If the read fails, or every instrument fails, it says so.
 */
import { fmtNum, fmtPct } from "../lib/format";
import { useApiQuery } from "../lib/query";
import type { Envelope } from "../lib/types";

/** Indices, vol, the curve by ETF, credit, commodities, crypto. */
export const TAPE_TICKERS = ["SPY", "QQQ", "DIA", "IWM", "^VIX", "SHY", "IEF", "TLT", "LQD", "HYG", "GLD", "USO", "BTC-USD"];

interface TapeRow {
  ticker: string;
  error: string | null;
  last?: number | null;
  ret_1d?: number | null;
}
interface TapeOut extends Envelope {
  as_of: string | null;
  rows: TapeRow[];
}

export function useTape() {
  return useApiQuery<TapeOut>("/market/overview", { tickers: TAPE_TICKERS.join(",") }, { refetchInterval: 5 * 60_000 });
}

const label = (t: string) => t.replace(/^\^/, "").replace(/-USD$/, "");

function Items({ rows }: { rows: TapeRow[] }) {
  return (
    <>
      {rows.map((r) => {
        const neg = typeof r.ret_1d === "number" && r.ret_1d < 0;
        return (
          <span key={r.ticker} className="oc-tape-item">
            <span className="oc-tape-sym">{label(r.ticker)}</span> {fmtNum(r.last, 2)}{" "}
            <span className={neg ? "oc-tape-chg loss" : "oc-tape-chg"}>{fmtPct(r.ret_1d, 2, { signed: true })}</span>
            <span className="oc-tape-sep" aria-hidden>
              │
            </span>
          </span>
        );
      })}
    </>
  );
}

export function Tape() {
  const q = useTape();
  const rows = (q.data?.rows ?? []).filter((r) => !r.error && typeof r.last === "number");
  let body;
  if (q.isError || (q.data && rows.length === 0)) body = <span className="oc-tape-msg">TAPE UNAVAILABLE</span>;
  else if (!q.data) body = <span className="oc-tape-msg">TAPE …</span>;
  else
    body = (
      <div className="oc-tape-track" role="marquee" aria-label="Market tape, one-day change">
        <span className="oc-tape-run">
          <Items rows={rows} />
        </span>
        <span className="oc-tape-run" aria-hidden>
          <Items rows={rows} />
        </span>
      </div>
    );
  return (
    <div className="oc-tape num">
      {body}
    </div>
  );
}
