/**
 * The session tape: the day so far — each limit's utilisation, and day P&L against −VaR.
 * Takes TapeColumns (model.ts), whichever source filled them: the flight recorder
 * (GET /deck/tape), the page's own trail of readings, or the engine's history.
 */
import { useMemo } from "react";
import { EmptyState, TimeSeriesChart, type LineSeries } from "../../components";
import type { TapeColumns } from "./model";

const MAX_SERIES = 8; // the categorical palette's length; the rest are named, not drawn

export function SessionTape({ tape, emptyText }: { tape: TapeColumns; emptyText: string }) {
  const names = Object.keys(tape.utilisation);
  const util = useMemo<LineSeries[]>(() => Object.keys(tape.utilisation).slice(0, MAX_SERIES).map((n) => ({ name: n, x: tape.ts, y: tape.utilisation[n] })), [tape]);
  const pnl = useMemo<LineSeries[]>(
    () => [
      { name: tape.pnlLabel, x: tape.ts, y: tape.day_pnl_usd, color: "var(--c1)" },
      { name: "−VaR", x: tape.ts, y: tape.var_usd.map((v) => (v == null ? null : -v)), color: "var(--loss)", dash: "dot" },
    ],
    [tape],
  );
  if (!tape.ts.length) return <EmptyState icon="line" title="Nothing on the tape yet" compact>{emptyText}</EmptyState>;
  return (
    <div className="grid-2">
      <div className="dk-tape-chart">
        <div className="dk-mini">Limit utilisation</div>
        {util.length ? (
          <TimeSeriesChart series={util} yFormat="pct" digits={0} height={240} baseline={1} />
        ) : (
          <p className="subtle small">This source records no per-limit utilisation.</p>
        )}
        {names.length > MAX_SERIES && <p className="subtle small">Not drawn (the palette holds eight): {names.slice(MAX_SERIES).join(", ")}.</p>}
      </div>
      <div className="dk-tape-chart">
        <div className="dk-mini">{tape.pnlLabel} and −VaR ($)</div>
        <TimeSeriesChart series={pnl} yFormat="usd" digits={0} height={240} baseline={0} />
      </div>
    </div>
  );
}
