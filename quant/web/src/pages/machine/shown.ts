/**
 * How a node's short label (`l`) is written on the page: one rule, for the scene's labels, the chips and the list.
 * The server's ids and labels are not changed, and search still matches the raw label as well. Pure.
 */
import type { MNode } from "./types";

/**
 * A node's label in plain words. A farm cell's label is its key ("dual_momentum@international"), which reads as
 * the code it is, so "@" is read as " on " and the underscores as spaces ("dual momentum on international").
 * Every other kind's label is already words and comes back as it is.
 */
export function shownLabel(n: Pick<MNode, "k" | "l">): string {
  return n.k === "result" ? n.l.replace(/@/g, " on ").replace(/_/g, " ") : n.l;
}

/** The tracks whose series carry a ticker's label, the same as its daily bars' ("AAPL"), so a chip must say which it is. */
const TRACK_WORD = new Map([["minute", "minute"], ["options", "options"], ["filings", "filings"]]);

/**
 * A node's label on a chip: `shownLabel`, a product's plain name (its label is a code: "SO", "DR"), and for a series
 * that is not the ticker's daily bars, its track ("AAPL minute", "AAPL options", "AAPL filings"). A daily series, and
 * the series whose labels are their own names (a FRED series, a factor, a filer), stay as they are.
 */
export function chipLabel(n: Pick<MNode, "k" | "l" | "n" | "m">): string {
  if (n.k === "product" && n.n) return n.n;
  const track = n.k === "series" && typeof n.m.track === "string" ? TRACK_WORD.get(n.m.track) : undefined;
  return track ? `${shownLabel(n)} ${track}` : shownLabel(n);
}
