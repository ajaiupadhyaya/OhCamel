/**
 * Classic calls | strike | puts option chain. Bid and ask cells are buttons: click the ask
 * to buy one contract, the bid to sell one (adds a leg to the strategy ticket). OI and
 * volume are drawn as bars behind the numbers; in-the-money cells are tinted and the
 * forward is marked between strikes.
 */
import { Fragment, useEffect, useMemo, useRef } from "react";
import { InfoTip, type InfoProp } from "../../components";
import { fmtCompact, fmtNum, fmtPct } from "../../lib/format";
import { INFO } from "./info";
import type { Chain, Quote } from "./types";

export type ChainView = "pricing" | "greeks";
type ColKey = "oi" | "volume" | "vendor_iv" | "iv" | "delta" | "gamma" | "vega" | "theta_day" | "bid" | "ask";

const COLS: Record<ChainView, ColKey[]> = {
  pricing: ["oi", "volume", "vendor_iv", "iv", "delta", "bid", "ask"],
  greeks: ["theta_day", "vega", "gamma", "delta", "iv", "bid", "ask"],
};

const HEAD: Record<ColKey, { label: string; info?: InfoProp; hide?: 600 | 900 | 1200 }> = {
  oi: { label: "OI", info: { title: "Open interest", text: "Contracts outstanding at the start of the day. The bar is scaled to the largest open interest in the chain." }, hide: 900 },
  volume: { label: "Vol", info: { title: "Volume", text: "Contracts traded today. The bar is scaled to the busiest strike." }, hide: 900 },
  vendor_iv: { label: "Vendor", info: INFO.vendor_iv, hide: 1200 },
  iv: { label: "IV", info: INFO.iv },
  delta: { label: "Δ", info: INFO.delta, hide: 600 },
  gamma: { label: "Γ", info: INFO.gamma, hide: 900 },
  vega: { label: "Vega", info: { ...INFO.vega, text: "Change in option value for one vol point (per option, per 1% of vol)." }, hide: 900 },
  theta_day: { label: "Θ/day", info: INFO.theta, hide: 1200 },
  bid: { label: "Bid" },
  ask: { label: "Ask" },
};

const FLAG_TEXT: [keyof Quote, string][] = [
  ["no_quote", "no two-sided quote"],
  ["zero_bid", "zero bid"],
  ["crossed", "crossed (bid > ask)"],
  ["wide", "wide spread"],
  ["stale", "stale"],
  ["arb_bound", "outside no-arbitrage bounds"],
];

export interface RowPair {
  strike: number;
  C?: Quote;
  P?: Quote;
}

export function chainRows(c: Chain): RowPair[] {
  const m = new Map<number, RowPair>();
  for (const q of c.quotes) {
    const r = m.get(q.strike) ?? { strike: q.strike };
    if (!r[q.type]) r[q.type] = q;
    m.set(q.strike, r);
  }
  return [...m.values()].sort((a, b) => a.strike - b.strike);
}

export function ChainTable({ c, rows, view, onTrade, maxHeight = 600 }: { c: Chain; rows: RowPair[]; view: ChainView; onTrade: (q: Quote, side: "buy" | "sell") => void; maxHeight?: number }) {
  const F = c.slice.forward;
  const cols = COLS[view];
  const wrap = useRef<HTMLDivElement>(null);
  const maxOi = useMemo(() => Math.max(1, ...c.quotes.map((q) => q.open_interest ?? 0)), [c]);
  const maxVol = useMemo(() => Math.max(1, ...c.quotes.map((q) => q.volume ?? 0)), [c]);
  const fwdIdx = rows.findIndex((r) => r.strike >= F);

  // centre the forward on load / expiry change
  useEffect(() => {
    const el = wrap.current;
    const tr = el?.querySelector<HTMLTableRowElement>("tr.vx-fwd-row");
    if (el && tr) el.scrollTop = Math.max(0, tr.offsetTop - el.clientHeight / 2);
  }, [c.expiry, rows.length]);

  const hideCls = (k: ColKey) => (HEAD[k].hide ? `oc-hide-below-${HEAD[k].hide}` : "");

  const cell = (q: Quote | undefined, k: ColKey, side: "C" | "P") => {
    const key = `${side}-${k}`;
    if (!q) return <td key={key} className={`vx-ch-cell ${hideCls(k)}`} />;
    const itm = side === "C" ? q.strike < F : q.strike > F;
    const cls = `vx-ch-cell num ${itm ? "itm" : ""} ${hideCls(k)}`;
    switch (k) {
      case "bid":
      case "ask": {
        const px = q[k];
        const can = px != null && px > 0;
        const action = k === "ask" ? "buy" : "sell";
        return (
          <td key={key} className={`${cls} vx-ch-px`}>
            <button type="button" className={`vx-trade vx-trade-${action}`} disabled={!can} onClick={() => onTrade(q, action)} title={can ? `${action === "buy" ? "Buy" : "Sell"} 1 ${q.contract} at ${fmtNum(px, 2)} ${k}` : "No price"}>
              {fmtNum(px, 2)}
            </button>
          </td>
        );
      }
      case "oi":
      case "volume": {
        const v = k === "oi" ? q.open_interest : q.volume;
        const w = Math.round(((v ?? 0) / (k === "oi" ? maxOi : maxVol)) * 100);
        return (
          <td key={key} className={`${cls} vx-ch-barcell`}>
            <span className={`vx-ch-bar vx-ch-bar-${k} ${side === "C" ? "left" : "right"}`} style={{ width: `${w}%` }} aria-hidden />
            <span className="vx-ch-barval">{fmtCompact(v, 1)}</span>
          </td>
        );
      }
      case "iv": {
        const flags = FLAG_TEXT.filter(([f]) => q[f]).map(([, t]) => t);
        return (
          <td key={key} className={`${cls} ${q.use_smile ? "vx-ch-iv-used" : ""}`} title={flags.length ? `Flags: ${flags.join(", ")}` : q.use_smile ? "Used in the SVI fit" : undefined}>
            {q.iv != null ? fmtPct(q.iv, 1) : <span className="subtle">—</span>}
            {flags.length > 0 && <span className="vx-flag" aria-label={flags.join(", ")} />}
          </td>
        );
      }
      case "vendor_iv":
        return <td key={key} className={`${cls} subtle`}>{fmtPct(q.vendor_iv, 1)}</td>;
      case "delta":
        return <td key={key} className={cls}>{fmtNum(q.delta, 2)}</td>;
      case "gamma":
        return <td key={key} className={cls}>{fmtNum(q.gamma, 4)}</td>;
      case "vega":
        return <td key={key} className={cls}>{fmtNum(q.vega != null ? q.vega / 100 : null, 2)}</td>;
      case "theta_day":
        return <td key={key} className={cls}>{fmtNum(q.theta_day, 2)}</td>;
    }
  };

  const callCols = cols;
  const putCols = [...cols].reverse();
  return (
    <div className="vx-chain-wrap" ref={wrap} style={{ maxHeight }}>
      <table className="vx-chain">
        <thead>
          <tr className="vx-chain-group">
            <th colSpan={callCols.length} className="vx-chain-side calls">Calls</th>
            <th className="vx-chain-strike-h" />
            <th colSpan={putCols.length} className="vx-chain-side puts">Puts</th>
          </tr>
          <tr>
            {callCols.map((k) => (
              <th key={`hc-${k}`} className={hideCls(k)}>
                <span className="vx-th">{HEAD[k].label}<InfoTip info={HEAD[k].info} size={11} label={HEAD[k].label} /></span>
              </th>
            ))}
            <th className="vx-chain-strike-h">Strike</th>
            {putCols.map((k) => (
              <th key={`hp-${k}`} className={hideCls(k)}>
                <span className="vx-th">{HEAD[k].label}<InfoTip info={HEAD[k].info} size={11} label={HEAD[k].label} /></span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <Fragment key={r.strike}>
              {i === fwdIdx && (
                <tr className="vx-fwd-row">
                  <td colSpan={callCols.length * 2 + 1}>
                    <span className="vx-fwd-label num">Forward {fmtNum(F, 2)} · spot {fmtNum(c.spot, 2)}</span>
                  </td>
                </tr>
              )}
              <tr>
                {callCols.map((k) => cell(r.C, k, "C"))}
                <td className="vx-chain-strike num">{fmtNum(r.strike, r.strike % 1 ? 1 : 0)}</td>
                {putCols.map((k) => cell(r.P, k, "P"))}
              </tr>
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
