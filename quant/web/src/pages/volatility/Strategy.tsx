/**
 * Strategy builder: a ticket of legs (from chain clicks or presets) and the analysis of
 * POST /api/options/strategy: P&L at the first expiry and on intermediate dates, breakevens,
 * max P/L, risk-neutral probability of profit and net greeks.
 */
import { useMemo } from "react";
import { DataTable, Panel, type Column } from "../../components";
import { XYChart, type XYSeries } from "../../charts/XYChart";
import { Absent, Note } from "../../design";
import { fmtCurrency, fmtDate, fmtNum, fmtPct } from "../../lib/format";
import { focusWindow } from "./derive";
import { INFO } from "./info";
import { GreekGrid, KV, Readline, RunButton } from "./shared";
import type { Chain, LegOut, LegSpec, Quote, StrategyOut } from "./types";
import type { UseQueryResult } from "@tanstack/react-query";
import type { ApiError } from "../../lib/api";

// ------------------------------------------------------------------ presets
export const PRESETS = [
  { id: "straddle", label: "STRADDLE", text: "Long ATM call + put: pays off on a big move either way; loses time value if the price sits still." },
  { id: "strangle", label: "STRANGLE", text: "Long 25Δ put + 25Δ call: a cheaper straddle that needs a bigger move." },
  { id: "vertical", label: "CALL SPREAD", text: "Long ATM call, short 25Δ call: a capped bet on a rise that costs less than the call alone." },
  { id: "condor", label: "CONDOR", text: "Short 25Δ strangle with long 10Δ wings: collects premium if the price stays in a range, with limited risk." },
  { id: "collar", label: "COLLAR", text: "100 shares + long 25Δ put − short 25Δ call: downside protection financed by giving up upside." },
] as const;
export type PresetId = (typeof PRESETS)[number]["id"];

function nearestBy(qs: Quote[], f: (q: Quote) => number | null): Quote | undefined {
  let best: Quote | undefined;
  let bd = Infinity;
  for (const q of qs) {
    const v = f(q);
    if (v == null || !Number.isFinite(v)) continue;
    if (v < bd) {
      bd = v;
      best = q;
    }
  }
  return best;
}

/** Build a preset from the chain of the selected expiry (strikes picked by delta / forward). */
export function buildPreset(id: PresetId, c: Chain): LegSpec[] {
  const ok = c.quotes.filter((q) => q.valid && (q.mid ?? 0) > 0 && q.delta != null);
  const calls = ok.filter((q) => q.type === "C");
  const puts = ok.filter((q) => q.type === "P");
  const F = c.slice.forward;
  const atmC = nearestBy(calls, (q) => Math.abs(q.strike - F));
  const atmP = nearestBy(puts, (q) => Math.abs(q.strike - F));
  const dC = (d: number) => nearestBy(calls.filter((q) => q.strike > F), (q) => Math.abs((q.delta as number) - d));
  const dP = (d: number) => nearestBy(puts.filter((q) => q.strike < F), (q) => Math.abs((q.delta as number) + d));
  const leg = (q: Quote | undefined, qty: number): LegSpec[] => (q ? [{ kind: "option", qty, expiry: c.expiry, strike: q.strike, type: q.type }] : []);
  switch (id) {
    case "straddle":
      return [...leg(atmC, 1), ...leg(atmP, 1)];
    case "strangle":
      return [...leg(dP(0.25), 1), ...leg(dC(0.25), 1)];
    case "vertical":
      return [...leg(atmC, 1), ...leg(dC(0.25), -1)];
    case "condor":
      return [...leg(dP(0.1), 1), ...leg(dP(0.25), -1), ...leg(dC(0.25), -1), ...leg(dC(0.1), 1)];
    case "collar":
      return [{ kind: "stock", qty: 100 }, ...leg(dP(0.25), 1), ...leg(dC(0.25), -1)];
  }
}

const legKey = (l: LegSpec) => (l.kind === "stock" ? "stock" : `${l.expiry}|${l.strike}|${l.type}`);

/** Add a leg, merging quantities with an identical existing leg (dropping it at zero). */
export function addLeg(legs: LegSpec[], l: LegSpec): LegSpec[] {
  const i = legs.findIndex((x) => legKey(x) === legKey(l));
  if (i < 0) return [...legs, l].slice(0, 12);
  const qty = legs[i].qty + l.qty;
  return qty === 0 ? legs.filter((_, j) => j !== i) : legs.map((x, j) => (j === i ? { ...x, qty } : x));
}

// ------------------------------------------------------------------ ticket
export function Ticket({ legs, setLegs, chain, onPreset, run, dirty, busy }: { legs: LegSpec[]; setLegs: (l: LegSpec[]) => void; chain: Chain | undefined; onPreset: (id: PresetId) => void; run: () => void; dirty: boolean; busy: boolean }) {
  const mid = (l: LegSpec) => (chain && l.expiry === chain.expiry ? chain.quotes.find((q) => q.type === l.type && q.strike === l.strike)?.mid : undefined);
  const setQty = (i: number, qty: number) => setLegs(qty === 0 ? legs.filter((_, j) => j !== i) : legs.map((x, j) => (j === i ? { ...x, qty } : x)));
  return (
    <div className="vx-ticket">
      <div className="vx-ticket-presets">
        {PRESETS.map((p) => (
          <button key={p.id} type="button" className="btn btn-sm btn-ghost" onClick={() => onPreset(p.id)} disabled={!chain} title={p.text}>
            {p.label}
          </button>
        ))}
      </div>
      {legs.length === 0 ? (
        <div className="vx-ticket-empty num">NO LEGS · ASK BUYS · BID SELLS</div>
      ) : (
        <ul className="vx-legs">
          {legs.map((l, i) => {
            const step = l.kind === "stock" ? 100 : 1;
            const m = mid(l);
            return (
              <li key={legKey(l)} className="vx-leg">
                <span className={`vx-leg-side ${l.qty > 0 ? "buy" : "sell"}`}>{l.qty > 0 ? "BUY" : "SELL"}</span>
                <span className="vx-leg-qty">
                  <button type="button" className="vx-step" aria-label="Decrease" onClick={() => setQty(i, l.qty - step)}>
                    −
                  </button>
                  <span className="num">{Math.abs(l.qty)}</span>
                  <button type="button" className="vx-step" aria-label="Increase" onClick={() => setQty(i, l.qty + step)}>
                    +
                  </button>
                </span>
                <span className="vx-leg-desc">
                  {l.kind === "stock" ? (
                    <span className="num">SHARES</span>
                  ) : (
                    <span className="num">
                      {fmtNum(l.strike, l.strike! % 1 ? 1 : 0)} {l.type === "C" ? "CALL" : "PUT"} · {fmtDate(l.expiry, "short").toUpperCase()}
                    </span>
                  )}
                </span>
                <span className="vx-leg-mid num">{m != null ? fmtNum(m, 2) : ""}</span>
                <button type="button" className="vx-step" aria-label="Remove leg" onClick={() => setLegs(legs.filter((_, j) => j !== i))}>
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="vx-ticket-actions">
        <button type="button" className="btn btn-sm btn-ghost" disabled={!legs.length} onClick={() => setLegs(legs.map((l) => ({ ...l, qty: -l.qty })))}>
          FLIP
        </button>
        <button type="button" className="btn btn-sm btn-ghost" disabled={!legs.length} onClick={() => setLegs([])}>
          CLEAR
        </button>
        <span className="spacer" />
        <RunButton onRun={run} dirty={dirty && legs.some((l) => l.kind === "option")} busy={busy} label="ANALYZE" />
      </div>
      <div className="vx-ticket-foot num">CONTRACT × {chain?.multiplier ?? 100} SH · STOCK IN SH · MID AT SELECTED EXPIRY</div>
    </div>
  );
}

// ------------------------------------------------------------------ results
const money = (v: number | null | undefined, signed = false) => fmtCurrency(v, { digits: 0, signed });

export function StrategyResults({ q }: { q: UseQueryResult<StrategyOut, ApiError> }) {
  if (!q.data && !q.isLoading && !q.error)
    return (
      <Panel title="Position">
        <Absent reason="NO POSITION · ADD LEGS · ANALYZE" source="POST /api/options/strategy" />
      </Panel>
    );
  return (
    <Panel<StrategyOut>
      title={
        <>
          {q.data ? `Position · P&L to ${fmtDate(q.data.horizon.expiry).toUpperCase()} · ${fmtNum(q.data.horizon.days, 0)}D` : "Position"}
          <Note n={1} to="strategy-payoff" />
        </>
      }
      query={q}
      skeletonHeight={440}
      asOf={q.data?.as_of}
    >
      {(d) => <Results d={d} />}
    </Panel>
  );
}

function Results({ d }: { d: StrategyOut }) {
  const { x, series } = useMemo(() => {
    const [lo, hi] = focusWindow({ spot: d.spot, breakevens: d.breakevens, strikes: d.legs.map((l) => l.strike).filter((k): k is number => k != null), grid: [d.grid.spot[0], d.grid.spot[d.grid.spot.length - 1]] });
    const keep = d.grid.spot.map((v, i) => (v >= lo && v <= hi ? i : -1)).filter((i) => i >= 0);
    const pick = (a: (number | null)[]) => keep.map((i) => a[i] ?? null);
    const curves = Object.entries(d.grid.curves).sort((a, b) => a[1].t_years - b[1].t_years);
    const out: XYSeries[] = curves
      .filter(([k]) => k !== "today" && k !== "expiry")
      .map(([k, c]) => ({ name: k.toUpperCase(), y: pick(c.pnl), tone: "ink3" as const, width: 1 }));
    if (d.grid.curves.today) out.push({ name: "TODAY", y: pick(d.grid.curves.today.pnl), tone: "ink2", dash: "dash" });
    if (d.grid.curves.expiry) out.push({ name: "EXPIRY", y: pick(d.grid.curves.expiry.pnl), tone: "ink", width: 1.75 });
    return { x: keep.map((i) => d.grid.spot[i]), series: out };
  }, [d]);
  const g = d.greeks;
  return (
    <div className="stack">
      <div className="vx-strat">
        <div className="vx-strat-chart">
          <XYChart
            x={x}
            series={series}
            xFormat="num"
            yFormat="usd"
            digits={0}
            height={360}
            xTitle={`${d.underlying} price`}
            hlines={[{ at: 0, label: "0", tone: "ink2", dash: "solid" }]}
            vlines={[{ at: d.spot, label: "SPOT", tone: "ink2", dash: "dot" }, ...d.breakevens.map((b) => ({ at: b, label: `BE ${fmtNum(b, 2)}`, tone: "ink3" as const, dash: "dash" as const }))]}
            ariaLabel={`Strategy P&L across ${d.underlying} prices`}
          />
        </div>
        <div className="vx-strat-side">
          <KV
            rows={[
              { label: `Net ${d.cost.direction}`, value: money(Math.abs(d.cost.mid)), hint: `NATURAL ${money(Math.abs(d.cost.natural))}` },
              { label: "Spread cost", value: money(d.cost.friction), info: INFO.friction },
              { label: "Max profit", value: d.max_profit == null ? "UNLIMITED" : money(d.max_profit) },
              { label: "Max loss", value: d.max_loss == null ? "UNLIMITED" : money(d.max_loss), tone: "loss" },
              { label: "Breakevens", value: d.breakevens.length ? d.breakevens.map((b) => fmtNum(b, 2)).join(" · ") : "NONE" },
              { label: "P(profit) · Q", value: fmtPct(d.prob_profit, 1), info: { ...INFO.pop, text: `${INFO.pop.text} Here: ${d.density_source}.` } },
              { label: "E[P&L] · Q", value: money(d.expected_pnl_q, true), info: INFO.exp_pnl, tone: (d.expected_pnl_q ?? 0) < 0 ? "loss" : "" },
            ]}
          />
        </div>
      </div>
      <Readline items={[{ k: "VALUATION", v: "BSM · STICKY STRIKE · OWN IV, r, q PER LEG" }]} />
      <GreekGrid
        cells={[
          { label: "Delta · sh", value: fmtNum(g.delta, 1, { signed: true }), info: INFO.delta, caption: `${fmtCurrency(g.dollar_delta, { compact: true, signed: true })} NOTIONAL` },
          { label: "Gamma · 1%", value: money(g.dollar_gamma_1pct, true), info: { ...INFO.gamma, formula: "\\tfrac12\\,\\Gamma\\,(0.01\\,S)^2" } },
          { label: "Vega · 1 pt", value: money(g.vega_per_vol_pt, true), info: INFO.vega },
          { label: "Theta · day", value: money(g.theta_day, true), info: INFO.theta },
          { label: "Rho · 1%", value: money(g.rho_per_pct, true), info: INFO.rho },
          { label: "Vanna", value: fmtNum(g.vanna, 2, { signed: true }), info: INFO.vanna },
        ]}
      />
      <LegsTable legs={d.legs} />
    </div>
  );
}

function LegsTable({ legs }: { legs: LegOut[] }) {
  const cols: Column<LegOut>[] = [
    { key: "qty", label: "Qty", numeric: true, format: (v) => fmtNum(v, 0, { signed: true }) },
    { key: "contract", label: "Contract", render: (l) => <span className="num">{l.kind === "stock" ? "SHARES" : l.contract}</span> },
    { key: "strike", label: "Strike", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "dte", label: "Days", numeric: true, format: (v) => fmtNum(v, 0), hideBelow: 600 },
    { key: "bid", label: "Bid", numeric: true, format: (v) => fmtNum(v, 2), hideBelow: 900 },
    { key: "ask", label: "Ask", numeric: true, format: (v) => fmtNum(v, 2), hideBelow: 900 },
    { key: "mid", label: "Mid", numeric: true, value: (l) => l.mid ?? l.price, format: (v) => fmtNum(v, 2) },
    { key: "iv", label: "IV", numeric: true, format: (v) => fmtPct(v, 1), info: INFO.iv },
    { key: "delta", label: "Δ (pos.)", numeric: true, format: (v) => fmtNum(v, 1, { signed: true }), hideBelow: 600 },
  ];
  return <DataTable columns={cols} rows={legs} rowKey={(l, i) => `${l.contract ?? "stock"}-${i}`} />;
}
