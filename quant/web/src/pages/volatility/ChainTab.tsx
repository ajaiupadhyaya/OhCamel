/**
 * Chain & strategies: the option chain of one expiry (GET /api/options/chain/{t}) with
 * filters, a strategy ticket fed by clicking quotes or presets, and the analysis of the
 * ticket (POST /api/options/strategy, run on "Analyze").
 */
import { useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Panel, SegmentedControl, Select, Toggle } from "../../components";
import type { ApiError } from "../../lib/api";
import { useMediaQuery } from "../../lib/hooks";
import { fmtNum, fmtPct } from "../../lib/format";
import { ChainTable, chainRows, type ChainView } from "./ChainTable";
import { StrategyResults, Ticket, addLeg, buildPreset, type PresetId } from "./Strategy";
import { expiryLabel, type useChain } from "./shared";
import type { Chain, LegSpec, StrategyOut } from "./types";

const WINDOWS = [
  { value: "0.05", label: "±5%" },
  { value: "0.1", label: "±10%" },
  { value: "0.2", label: "±20%" },
  { value: "all", label: "All" },
] as const;
type Win = (typeof WINDOWS)[number]["value"];

export const SPREAD_FILTERS = [
  { value: "0.25", label: "Spread ≤ 25% of mid" },
  { value: "0.5", label: "Spread ≤ 50% of mid" },
  { value: "1", label: "Spread ≤ 100% of mid" },
  { value: "2", label: "Spread ≤ 200% of mid" },
];

export function ChainTab({ chain, spread, setSpread, legs, setLegs, strategy, run, dirty }: { chain: ReturnType<typeof useChain>; spread: string; setSpread: (v: string) => void; legs: LegSpec[]; setLegs: (l: LegSpec[]) => void; strategy: UseQueryResult<StrategyOut, ApiError>; run: () => void; dirty: boolean }) {
  const [win, setWin] = useState<Win>("0.1");
  const [view, setView] = useState<ChainView>("pricing");
  const [twoSided, setTwoSided] = useState(true);
  const c = chain.data;
  const rows = useMemo(() => {
    if (!c) return [];
    const F = c.slice.forward;
    return chainRows(c).filter((r) => {
      if (win !== "all" && Math.abs(r.strike / F - 1) > +win) return false;
      if (twoSided && !(r.C?.valid || r.P?.valid)) return false;
      return true;
    });
  }, [c, win, twoSided]);

  const onPreset = (id: PresetId) => c && setLegs(buildPreset(id, c));
  // the full chain needs ~1150px; beside it the ticket only fits on very wide screens
  const wide = useMediaQuery("(min-width: 1640px)");
  const ticket = (
    <Panel title="Strategy ticket" subtitle="Build a position, then analyze it at live mid prices." info={{ text: "Up to 12 legs across any expiries. Clicking a price adds one contract; clicking the same side again adds another, the opposite side reduces it." }} className="vx-ticket-panel" id="vx-ticket">
      <Ticket legs={legs} setLegs={setLegs} chain={c} onPreset={onPreset} run={run} dirty={dirty} busy={strategy.isFetching} />
    </Panel>
  );

  return (
    <>
      <div className={wide ? "vx-chain-grid" : ""}>
        <Panel<Chain>
          title={c ? `Chain · ${expiryLabel({ expiry: c.slice.expiry, dte: c.slice.dte, settlement: c.slice.settlement })}` : "Chain"}
          subtitle={c ? <ChainSubtitle c={c} shown={rows.length} /> : "Calls on the left, puts on the right, strikes down the middle."}
          info={{ title: "Reading the chain", text: "Each row is one strike. IV is our own implied vol from the mid price using this expiry's parity-implied forward and discount factor; Vendor is Cboe's figure for comparison. Tinted cells are in the money. A small ochre dot flags a quote that failed a cleaning filter (hover the IV for the reason).", reference: "Black (1976); put-call parity: Stoll (1969)" }}
          query={chain}
          flush
          actions={
            <>
              <SegmentedControl size="sm" ariaLabel="Moneyness window" options={WINDOWS.map((w) => ({ value: w.value, label: w.label, title: w.value === "all" ? "Every listed strike" : `Strikes within ${w.label} of the forward` }))} value={win} onChange={setWin} />
              {!wide && (
                <button type="button" className="btn btn-sm" onClick={() => document.getElementById("vx-ticket")?.scrollIntoView({ behavior: "smooth", block: "start" })} title="Jump to the strategy ticket">
                  Ticket · {legs.length} leg{legs.length === 1 ? "" : "s"}
                </button>
              )}
              <SegmentedControl size="sm" ariaLabel="Columns" options={[{ value: "pricing", label: "Prices" }, { value: "greeks", label: "Greeks" }]} value={view} onChange={setView} />
            </>
          }
          skeletonHeight={560}
        >
          {(d) => (
            <>
              <div className="vx-chain-filters">
                <Toggle label="Only strikes with a usable quote" checked={twoSided} onChange={setTwoSided} />
                <Select ariaLabel="Maximum bid-ask spread" value={spread} onChange={setSpread} options={SPREAD_FILTERS} />
                <span className="subtle small">Quotes wider than this are flagged and kept out of IVs and fits.</span>
              </div>
              <ChainTable c={d} rows={rows} view={view} onTrade={(q, side) => setLegs(addLeg(legs, { kind: "option", qty: side === "buy" ? 1 : -1, expiry: d.expiry, strike: q.strike, type: q.type }))} />
            </>
          )}
        </Panel>
        {wide && ticket}
      </div>
      {wide ? (
        <StrategyResults q={strategy} />
      ) : (
        <div className="vx-strat-grid">
          {ticket}
          <StrategyResults q={strategy} />
        </div>
      )}
    </>
  );
}

function ChainSubtitle({ c, shown }: { c: Chain; shown: number }) {
  const s = c.slice;
  return (
    <span className="vx-chain-sub">
      <span>F <b className="num">{fmtNum(s.forward, 2)}</b></span>
      <span>r <b className="num">{fmtPct(s.rate, 2)}</b></span>
      <span>q <b className="num">{fmtPct(s.div_yield, 2)}</b></span>
      <span>{s.n_valid} of {s.n_contracts} quotes usable · {shown} strikes shown</span>
      <span>{c.exercise_style}</span>
    </span>
  );
}
