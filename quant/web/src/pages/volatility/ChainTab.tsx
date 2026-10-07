/**
 * CHAIN: one expiry's chain (GET /api/options/chain/{t}) with moneyness and spread filters,
 * a ticket fed by clicking quotes or presets, and the ticket's analysis (POST
 * /api/options/strategy, run on ANALYZE).
 */
import { useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { Panel, SegmentedControl, Select, Toggle } from "../../components";
import type { ApiError } from "../../lib/api";
import { useMediaQuery } from "../../lib/hooks";
import { fmtNum, fmtPct } from "../../lib/format";
import { Note } from "../../design";
import { ChainTable, chainRows, type ChainView } from "./ChainTable";
import { StrategyResults, Ticket, addLeg, buildPreset, type PresetId } from "./Strategy";
import { Readline, expiryLabel, type useChain } from "./shared";
import type { Chain, LegSpec, StrategyOut } from "./types";

const WINDOWS = [
  { value: "0.05", label: "±5%" },
  { value: "0.1", label: "±10%" },
  { value: "0.2", label: "±20%" },
  { value: "all", label: "ALL" },
] as const;
type Win = (typeof WINDOWS)[number]["value"];

export const SPREAD_FILTERS = [
  { value: "0.25", label: "SPREAD ≤ 25% MID" },
  { value: "0.5", label: "SPREAD ≤ 50% MID" },
  { value: "1", label: "SPREAD ≤ 100% MID" },
  { value: "2", label: "SPREAD ≤ 200% MID" },
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
    <Panel title="Ticket · ≤ 12 legs" className="vx-ticket-panel" id="vx-ticket">
      <Ticket legs={legs} setLegs={setLegs} chain={c} onPreset={onPreset} run={run} dirty={dirty} busy={strategy.isFetching} />
    </Panel>
  );

  return (
    <>
      <div className={wide ? "vx-chain-grid" : ""}>
        <Panel<Chain>
          title={
            <>
              {c ? `Chain · ${expiryLabel({ expiry: c.slice.expiry, dte: c.slice.dte, settlement: c.slice.settlement })}` : "Chain"}
              <Note n={1} to="implied-vol" />
            </>
          }
          query={chain}
          asOf={c?.as_of}
          flush
          actions={
            <>
              <SegmentedControl size="sm" ariaLabel="Moneyness window" options={WINDOWS.map((w) => ({ value: w.value, label: w.label }))} value={win} onChange={setWin} />
              {!wide && (
                <button type="button" className="btn btn-sm" onClick={() => document.getElementById("vx-ticket")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
                  TICKET · {legs.length}
                </button>
              )}
              <SegmentedControl size="sm" ariaLabel="Columns" options={[{ value: "pricing", label: "PRICES" }, { value: "greeks", label: "GREEKS" }]} value={view} onChange={setView} />
            </>
          }
          skeletonHeight={560}
        >
          {(d) => (
            <>
              <ChainLine c={d} shown={rows.length} />
              <div className="vx-chain-filters">
                <Toggle label="USABLE QUOTES ONLY" checked={twoSided} onChange={setTwoSided} />
                <Select ariaLabel="Maximum bid-ask spread kept in IVs and fits" value={spread} onChange={setSpread} options={SPREAD_FILTERS} />
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

function ChainLine({ c, shown }: { c: Chain; shown: number }) {
  const s = c.slice;
  return (
    <Readline
      items={[
        { k: "F", v: fmtNum(s.forward, 2) },
        { k: "r", v: fmtPct(s.rate, 2) },
        { k: "q", v: fmtPct(s.div_yield, 2) },
        { k: "USABLE", v: `${s.n_valid}/${s.n_contracts}` },
        { k: "STRIKES", v: fmtNum(shown, 0) },
        { k: "STYLE", v: c.exercise_style.toUpperCase() },
      ]}
    />
  );
}
