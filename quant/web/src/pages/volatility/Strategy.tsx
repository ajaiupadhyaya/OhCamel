/**
 * Strategy builder: a ticket of legs (from chain clicks or presets) and the analysis of
 * POST /api/options/strategy — payoff at the first expiry and on intermediate dates,
 * breakevens, max P/L, risk-neutral probability of profit and net greeks.
 */
import { useMemo } from "react";
import type { Data } from "plotly.js";
import { Chart, DataTable, EmptyState, Icon, Panel, withAlpha, type Column } from "../../components";
import { fmtCurrency, fmtDate, fmtNum, fmtPct } from "../../lib/format";
import type { Tokens } from "../../lib/theme";
import { INFO } from "./info";
import { GreekGrid, KV, RunButton } from "./shared";
import type { Chain, LegOut, LegSpec, Quote, StrategyOut } from "./types";
import type { UseQueryResult } from "@tanstack/react-query";
import type { ApiError } from "../../lib/api";

// ------------------------------------------------------------------ presets
export const PRESETS = [
  { id: "straddle", label: "Straddle", text: "Long ATM call + put: pays off on a big move either way; loses time value if the price sits still." },
  { id: "strangle", label: "Strangle", text: "Long 25Δ put + 25Δ call: a cheaper straddle that needs a bigger move." },
  { id: "vertical", label: "Bull call spread", text: "Long ATM call, short 25Δ call: a capped bet on a rise that costs less than the call alone." },
  { id: "condor", label: "Iron condor", text: "Short 25Δ strangle with long 10Δ wings: collects premium if the price stays in a range, with limited risk." },
  { id: "collar", label: "Collar", text: "100 shares + long 25Δ put − short 25Δ call: downside protection financed by giving up upside." },
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
        <div className="vx-ticket-empty subtle small">Click an <b>ask</b> in the chain to buy, a <b>bid</b> to sell — or start from a preset.</div>
      ) : (
        <ul className="vx-legs">
          {legs.map((l, i) => {
            const step = l.kind === "stock" ? 100 : 1;
            const m = mid(l);
            return (
              <li key={legKey(l)} className="vx-leg">
                <span className={`vx-leg-side ${l.qty > 0 ? "buy" : "sell"}`}>{l.qty > 0 ? "Buy" : "Sell"}</span>
                <span className="vx-leg-qty">
                  <button type="button" className="icon-btn" aria-label="Decrease" onClick={() => setQty(i, l.qty - step)}>
                    <Icon name="minus" size={12} />
                  </button>
                  <span className="num">{Math.abs(l.qty)}</span>
                  <button type="button" className="icon-btn" aria-label="Increase" onClick={() => setQty(i, l.qty + step)}>
                    <Icon name="plus" size={12} />
                  </button>
                </span>
                <span className="vx-leg-desc">
                  {l.kind === "stock" ? (
                    <span>shares</span>
                  ) : (
                    <>
                      <span className="num">{fmtNum(l.strike, l.strike! % 1 ? 1 : 0)}</span> <span className={`vx-leg-type ${l.type}`}>{l.type === "C" ? "call" : "put"}</span>
                      <span className="subtle small"> · {fmtDate(l.expiry, "short")}</span>
                    </>
                  )}
                </span>
                <span className="vx-leg-mid num subtle small">{m != null ? fmtNum(m, 2) : ""}</span>
                <button type="button" className="icon-btn" aria-label="Remove leg" onClick={() => setLegs(legs.filter((_, j) => j !== i))}>
                  <Icon name="x" size={13} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <div className="vx-ticket-actions">
        <button type="button" className="btn btn-sm btn-ghost" disabled={!legs.length} onClick={() => setLegs(legs.map((l) => ({ ...l, qty: -l.qty })))} title="Turn every buy into a sell and vice versa">
          Flip
        </button>
        <button type="button" className="btn btn-sm btn-ghost" disabled={!legs.length} onClick={() => setLegs([])}>
          <Icon name="trash" size={13} /> Clear
        </button>
        <span className="spacer" />
        <RunButton onRun={run} dirty={dirty && legs.some((l) => l.kind === "option")} busy={busy} label="Analyze" />
      </div>
      <p className="vx-ticket-foot subtle small">Option quantities are contracts (× {chain?.multiplier ?? 100} shares); stock in shares. Mid shown for the selected expiry.</p>
    </div>
  );
}

// ------------------------------------------------------------------ results
const money = (v: number | null | undefined, signed = false) => fmtCurrency(v, { digits: 0, signed });

export function StrategyResults({ q }: { q: UseQueryResult<StrategyOut, ApiError> }) {
  return (
    <Panel<StrategyOut>
      title="Position analysis"
      subtitle={q.data ? `P&L at the first expiry (${fmtDate(q.data.horizon.expiry)}, ${fmtNum(q.data.horizon.days, 0)} days) and on the way there, from mid prices` : "Payoff, breakevens and risk of the ticket"}
      info={{ title: "How the position is valued", text: "Each option leg is repriced with Black–Scholes–Merton at its own implied vol, rate and dividend yield (sticky strike). At expiry, expiring legs pay intrinsic value. Probability of profit integrates the payoff against the risk-neutral density.", reference: "Black & Scholes (1973); Merton (1973); Derman (1999) on sticky strike; Breeden & Litzenberger (1978)" }}
      query={q}
      empty={!q.data && !q.isLoading && !q.error ? <EmptyState icon="options" title="No position yet">Add legs to the ticket and press Analyze.</EmptyState> : undefined}
      skeletonHeight={440}
    >
      {(d) => <Results d={d} />}
    </Panel>
  );
}

function Results({ d }: { d: StrategyOut }) {
  const curves = Object.entries(d.grid.curves).sort((a, b) => a[1].t_years - b[1].t_years);
  const data = useMemo(
    () => (t: Tokens): Data[] => {
      const x = d.grid.spot;
      const exp = d.grid.curves.expiry?.pnl ?? [];
      const tr: unknown[] = [
        { type: "scatter", mode: "lines", x, y: exp.map((v) => (v != null && v > 0 ? v : 0)), fill: "tozeroy", fillcolor: withAlpha(t.gain, 0.16), line: { width: 0 }, hoverinfo: "skip", showlegend: false },
        { type: "scatter", mode: "lines", x, y: exp.map((v) => (v != null && v < 0 ? v : 0)), fill: "tozeroy", fillcolor: withAlpha(t.loss, 0.16), line: { width: 0 }, hoverinfo: "skip", showlegend: false },
      ];
      const mids = curves.filter(([k]) => k !== "today" && k !== "expiry");
      mids.forEach(([k, c], i) => tr.push({ type: "scatter", mode: "lines", x, y: c.pnl, name: k, line: { color: withAlpha(t.categorical[0], 0.25 + 0.2 * i), width: 1 }, hovertemplate: `${k} <b>%{y:$,.0f}</b><extra></extra>` }));
      if (d.grid.curves.today) tr.push({ type: "scatter", mode: "lines", x, y: d.grid.curves.today.pnl, name: "Today", line: { color: t.categorical[0], width: 2, dash: "dash" }, hovertemplate: "today <b>%{y:$,.0f}</b><extra></extra>" });
      tr.push({ type: "scatter", mode: "lines", x, y: exp, name: "At expiry", line: { color: t.text, width: 2.2 }, hovertemplate: "expiry <b>%{y:$,.0f}</b><extra>%{x:,.2f}</extra>" });
      return tr as Data[];
    },
    [d, curves],
  );
  // open on the region that matters (strikes, breakevens, spot); the full grid is a zoom-out away
  const focus = useMemo(() => {
    const pts = [d.spot, ...d.breakevens, ...d.legs.map((l) => l.strike).filter((k): k is number => k != null)];
    const lo = Math.min(...pts);
    const hi = Math.max(...pts);
    const pad = Math.max((hi - lo) * 0.6, d.spot * 0.05);
    return [Math.max(d.grid.spot[0], lo - pad), Math.min(d.grid.spot[d.grid.spot.length - 1], hi + pad)];
  }, [d]);
  const layout = useMemo(
    () => (t: Tokens) => ({
      hovermode: "x unified",
      xaxis: { title: { text: `${d.underlying} price` }, tickformat: ",.0f", range: focus },
      yaxis: { tickformat: "$,.0f", side: "right", zeroline: true, zerolinecolor: t.ruleStrong },
      shapes: [
        { type: "line", yref: "paper", y0: 0, y1: 1, x0: d.spot, x1: d.spot, line: { color: t.text2, width: 1, dash: "dot" } },
        ...d.breakevens.map((b) => ({ type: "line", yref: "paper", y0: 0, y1: 1, x0: b, x1: b, line: { color: t.warn, width: 1, dash: "dash" } })),
      ],
      annotations: [
        { x: d.spot, yref: "paper", y: 1, text: "spot", showarrow: false, xanchor: "left", yanchor: "top", xshift: 4, font: { size: 10, color: t.text2 } },
        ...d.breakevens.map((b) => ({ x: b, yref: "paper", y: 0, text: `BE ${fmtNum(b, 2)}`, showarrow: false, xanchor: "left", yanchor: "bottom", xshift: 4, font: { size: 10, color: t.warn } })),
      ],
      margin: { l: 16, r: 8, t: 36, b: 44 },
    }),
    [d, focus],
  );
  const g = d.greeks;
  const unbounded = (v: number | null) => v == null;
  return (
    <div className="stack">
      <div className="vx-strat">
        <div className="vx-strat-chart">
          <Chart data={data} layout={layout as never} height={380} />
        </div>
        <div className="vx-strat-side">
          <KV
            rows={[
              { label: `Net ${d.cost.direction}`, value: money(Math.abs(d.cost.mid)), hint: `at mid · natural ${money(Math.abs(d.cost.natural))}`, info: { text: "What the position costs to open at mid prices (debit) or pays you (credit)." } },
              { label: "Spread cost", value: money(d.cost.friction), info: INFO.friction },
              { label: "Max profit", value: unbounded(d.max_profit) ? "Unlimited" : money(d.max_profit), tone: "gain", info: { text: "Best P&L at the first expiry across all prices; unlimited when the position gains without bound as the price rises." } },
              { label: "Max loss", value: unbounded(d.max_loss) ? "Unlimited" : money(d.max_loss), tone: "loss", info: { text: "Worst P&L at the first expiry across all prices; unlimited when losses grow without bound as the price rises." } },
              { label: "Breakevens", value: d.breakevens.length ? d.breakevens.map((b) => fmtNum(b, 2)).join(" · ") : "none", info: { text: "Prices at the first expiry where the P&L crosses zero." } },
              { label: "Prob. of profit (Q)", value: fmtPct(d.prob_profit, 1), info: { ...INFO.pop, text: `${INFO.pop.text} Here: ${d.density_source}.` }, hint: "risk-neutral" },
              { label: "Expected P&L (Q)", value: money(d.expected_pnl_q, true), info: INFO.exp_pnl },
            ]}
          />
        </div>
      </div>
      <GreekGrid
        cells={[
          { label: "Delta", value: `${fmtNum(g.delta, 1, { signed: true })} sh`, info: { ...INFO.delta, text: "Net share-equivalent exposure: the position gains about this many dollars for a $1 rise." }, caption: `${fmtCurrency(g.dollar_delta, { compact: true, signed: true })} notional` },
          { label: "Gamma · 1% move", value: money(g.dollar_gamma_1pct, true), info: { ...INFO.gamma, text: "Extra P&L from convexity for a 1% move in either direction.", formula: "\\tfrac12\\,\\Gamma\\,(0.01\\,S)^2" } },
          { label: "Vega · 1 vol pt", value: money(g.vega_per_vol_pt, true), info: INFO.vega },
          { label: "Theta · day", value: money(g.theta_day, true), info: INFO.theta },
          { label: "Rho · 1% rate", value: money(g.rho_per_pct, true), info: INFO.rho },
          { label: "Vanna", value: fmtNum(g.vanna, 2, { signed: true }), info: INFO.vanna },
        ]}
      />
      <LegsTable legs={d.legs} />
    </div>
  );
}

function LegsTable({ legs }: { legs: LegOut[] }) {
  const cols: Column<LegOut>[] = [
    { key: "qty", label: "Qty", numeric: true, format: (v) => fmtNum(v, 0, { signed: true }), color: "sign" },
    { key: "contract", label: "Contract", render: (l) => (l.kind === "stock" ? <span>Shares</span> : <span className="num">{l.contract}</span>) },
    { key: "strike", label: "Strike", numeric: true, format: (v) => fmtNum(v, 2) },
    { key: "dte", label: "Days", numeric: true, format: (v) => fmtNum(v, 0), hideBelow: 600 },
    { key: "bid", label: "Bid", numeric: true, format: (v) => fmtNum(v, 2), hideBelow: 900 },
    { key: "ask", label: "Ask", numeric: true, format: (v) => fmtNum(v, 2), hideBelow: 900 },
    { key: "mid", label: "Mid", numeric: true, value: (l) => l.mid ?? l.price, format: (v) => fmtNum(v, 2) },
    { key: "iv", label: "IV", numeric: true, render: (l) => <span title={l.iv_source}>{fmtPct(l.iv, 1)}</span>, info: INFO.iv },
    { key: "delta", label: "Δ (pos.)", numeric: true, format: (v) => fmtNum(v, 1, { signed: true }), hideBelow: 600 },
  ];
  return <DataTable columns={cols} rows={legs} rowKey={(l, i) => `${l.contract ?? "stock"}-${i}`} />;
}
