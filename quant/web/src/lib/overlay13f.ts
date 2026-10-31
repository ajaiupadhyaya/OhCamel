/**
 * A 13F's options as a stated assumption. The filing reports an option's underlying notional
 * and no strike or expiry, so nothing here prices one: a call is long and a put short at an
 * assumed |delta|, on the equity base the filing's stock weights use, netted by name.
 *
 * The overlay does not rescale with the stocks. The top-N stocks are rescaled to stand for the
 * whole equity book, and an overlay weight is already a fraction of that same book.
 */
import type { Holding } from "./types";
import type { Option13F } from "./market";

/** A portfolio holds at most 100 names, and the stock side may use sixty of them. */
export const OVERLAY_NAMES = 40;

export interface Overlay {
  holdings: Holding[];
  /** Names beyond OVERLAY_NAMES. */
  left_out: number;
  /** Option rows with no ticker, or no equity base to weigh them on. */
  unmapped: number;
}

export function optionOverlay(options: Option13F[], delta: number): Overlay {
  const net = new Map<string, number>();
  let unmapped = 0;
  for (const o of options) {
    if (!o.ticker || o.weight_on_equity == null) {
      unmapped += 1;
      continue;
    }
    const sign = o.put_call.toLowerCase() === "put" ? -1 : 1;
    net.set(o.ticker, (net.get(o.ticker) ?? 0) + sign * delta * o.weight_on_equity);
  }
  const named = [...net]
    .filter(([, weight]) => weight !== 0)
    .map(([ticker, weight]) => ({ ticker, weight }))
    .sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
  return { holdings: named.slice(0, OVERLAY_NAMES), left_out: Math.max(0, named.length - OVERLAY_NAMES), unmapped };
}

/** The stock book with the overlay added: a name in both nets, and one netted to zero goes. */
export function withOverlay(equity: Holding[], overlay: Holding[]): Holding[] {
  const m = new Map(equity.map((h) => [h.ticker, h.weight]));
  for (const o of overlay) m.set(o.ticker, (m.get(o.ticker) ?? 0) + o.weight);
  return [...m].filter(([, weight]) => weight !== 0).map(([ticker, weight]) => ({ ticker, weight }));
}
