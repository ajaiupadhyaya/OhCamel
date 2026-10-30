/**
 * The top bar's search box: which nodes a typed query finds, best first. Pure, no DOM.
 *
 * A node matches when the query is a case-insensitive substring of its id, label, name or kind. The match is
 * plain text, never a pattern, so a query such as `(`, `.*` or `farm.sweep` means exactly those characters.
 */
import type { NodeKind } from "./types";
import type { GraphIndex } from "./web";

/** The longest query that is searched. Longer text matches no id, label or name, so it is not tried. */
const MAX_QUERY = 120;

/**
 * Where a kind ranks among matches, best first (spec §6.7): the book, products, feeds, jobs, strategies,
 * results, then the series. The limits and the kill switch, which the spec does not place, follow the results:
 * they are few and sit inside the machine, where a series is one of hundreds. A kind the page does not know
 * comes last.
 */
const KIND_ORDER: readonly NodeKind[] = ["book", "product", "feed", "job", "strategy", "result", "limit", "kill", "series"];

const lower = (v: unknown): string => (typeof v === "string" ? v.toLowerCase() : "");

/**
 * The nodes that match `q`, as indices into `gi.out.nodes`, best first, at most `limit` of them.
 *
 * The order is: a node whose label is exactly `q` first; then by kind (`KIND_ORDER`); then, among series, the
 * daily series before the other tracks; then the shorter label; then the payload's own order. Empty or
 * whitespace-only text, text that is not a string and text too long to match anything find nothing.
 */
export function search(gi: GraphIndex, q: string, limit = 12): number[] {
  if (typeof q !== "string") return [];
  const text = q.trim().toLowerCase();
  if (text === "" || text.length > MAX_QUERY) return [];

  const hits: { i: number; exact: boolean; kind: number; other: boolean; len: number }[] = [];
  gi.out.nodes.forEach((n, i) => {
    if (![n.id, n.l, n.n, n.k].some((f) => lower(f).includes(text))) return;
    const kind = KIND_ORDER.indexOf(n.k);
    hits.push({
      i,
      exact: lower(n.l) === text,
      kind: kind < 0 ? KIND_ORDER.length : kind,
      other: n.k === "series" && n.m?.track !== "daily",
      len: typeof n.l === "string" ? n.l.length : Infinity,
    });
  });

  hits.sort((a, b) =>
    Number(b.exact) - Number(a.exact) ||
    a.kind - b.kind ||
    Number(a.other) - Number(b.other) ||
    (a.len === b.len ? 0 : a.len < b.len ? -1 : 1) ||
    a.i - b.i,
  );
  const cap = Number.isNaN(limit) ? 0 : Math.max(0, Math.floor(limit));
  return hits.slice(0, cap).map((h) => h.i);
}
