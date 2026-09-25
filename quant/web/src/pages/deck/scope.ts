/**
 * The targeting computer's layout — a TypeScript port of Figure 2's `web/scope.js`
 * (branch feature/scope; design: docs/superpowers/specs/2026-09-24-figure-2-the-scope-design.md).
 *
 * Every limit still ahead is a gate in a tunnel, at a depth set by its headroom; the fullest
 * is the target and the readout is its room to spare; a breached limit has passed the screen
 * and is a pair of red rails; a limit that cannot be evaluated has no depth, and is one dashed
 * gate beyond the vanishing point, because a limit drawn as far away reads as safe.
 *
 * `layout()` is arithmetic and touches no DOM, so scope.test.ts runs it under Vitest. The
 * drawing is TargetingComputer.tsx.
 */

/** A limit evaluation in the OCaml engine's wire shape (the deck's readings use it too). */
export interface ScopeLimit {
  name: string;
  unit: string; // "fraction" reads in basis points; anything else ("USD", "money") in dollars
  observed: number | null;
  threshold: number;
  excess: number | null; // observed − threshold: negative while there is room
  breached: boolean;
  utilisation: number | null; // observed / threshold; null where it is not finite
}

export interface ScopeGate {
  name: string;
  pct: string;
  depth: number;
  scale: number;
  stale: boolean;
  target: boolean;
}
export interface ScopeRail {
  name: string;
  stale: boolean;
  label: string;
}
export interface ScopeReadout {
  digits: string;
  unit: "bp" | "dollars" | null;
  over: boolean;
  stale: boolean;
  caption: string;
}
export interface ScopeLamp {
  name: string;
  state: "ahead" | "over" | "unknown";
  pct: string;
  stale: boolean;
}
export interface ScopeModel {
  gates: ScopeGate[]; // far to near: the draw order
  target: string | null;
  readout: ScopeReadout;
  rails: ScopeRail[]; // worst first (outermost)
  railsHidden: number;
  unknown: string[];
  lamps: ScopeLamp[];
  summary: string;
}

export const K = 5; // perspective: a gate at depth d is drawn at 1 / (1 + K d)
export const MAX_RAILS = 6; // breaches past this are counted, not drawn
export const UNKNOWN_DEPTH = 1.25; // beyond the vanishing point of an idle limit

const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));
const finite = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
export const depthOf = (u: number) => clamp(1 - u, 0, 1);
export const scaleOf = (d: number) => 1 / (1 + K * d);

const pad = (n: number, width: number) => String(n).padStart(width, "0");

// The readout's magnitude in the limit's own unit: whole dollars, or basis points for a
// fraction, because a drawdown is not a dollar figure.
function magnitude(l: ScopeLimit): { n: number; digits: string; unit: "bp" | "dollars" } {
  const x = Math.abs(l.excess ?? 0);
  return l.unit === "fraction"
    ? { n: Math.round(x * 10000), digits: pad(Math.round(x * 10000), 4), unit: "bp" }
    : { n: Math.round(x), digits: pad(Math.round(x), 6), unit: "dollars" };
}
function spoken(l: ScopeLimit): string {
  const m = magnitude(l);
  return m.unit === "bp" ? `${m.n} bp` : `$${m.n.toLocaleString("en-US")}`;
}
const pctOf = (l: ScopeLimit) => (finite(l.utilisation) ? `${Math.round(l.utilisation * 100)}%` : "—");

// How far over, for a rail's label. A breach just past the line would round to "+0%", which
// reads as no breach at all, so under ten percent it keeps a decimal.
function overPct(u: number): string {
  const o = (u - 1) * 100;
  return `${o < 10 ? o.toFixed(1) : String(Math.round(o))}%`;
}
// Sorting key for "fullest": a non-finite utilisation on a breach is the worst there is
// (a zero threshold with something against it).
function fullness(l: ScopeLimit): number {
  if (finite(l.utilisation)) return l.utilisation;
  return l.breached ? Infinity : 1;
}
function byFullnessThenName(a: ScopeLimit, b: ScopeLimit): number {
  const d = fullness(b) - fullness(a);
  if (d !== 0 && !Number.isNaN(d)) return d;
  return a.name < b.name ? -1 : a.name > b.name ? 1 : 0;
}

export const never = (_key: string) => false;

/**
 * @param isStale called with `"limit:" + name` (Figure 2's closure key), true where that
 *   limit's inputs are stale.
 */
export function layout(limits: ScopeLimit[] | null | undefined, unevaluated: string[] | null | undefined, isStale: (key: string) => boolean = never): ScopeModel {
  const ls = limits ?? [];
  const un = unevaluated ?? [];
  const staleOf = (name: string) => !!isStale(`limit:${name}`);

  const ahead = ls.filter((l) => !l.breached).sort(byFullnessThenName);
  const over = ls.filter((l) => l.breached).sort(byFullnessThenName);
  const target = ahead.length ? ahead[0] : null;

  // Far to near, the order they are drawn in, so a nearer gate is on top.
  const gates: ScopeGate[] = ahead
    .slice()
    .reverse()
    .map((l) => {
      const d = finite(l.utilisation) ? depthOf(l.utilisation) : 0;
      return { name: l.name, pct: pctOf(l), depth: d, scale: scaleOf(d), stale: staleOf(l.name), target: l === target };
    });

  const rails: ScopeRail[] = over.slice(0, MAX_RAILS).map((l) => ({
    name: l.name,
    stale: staleOf(l.name),
    label: finite(l.utilisation) ? `${l.name} +${overPct(l.utilisation)}` : `${l.name} over`,
  }));

  let readout: ScopeReadout;
  if (target) {
    const mt = magnitude(target);
    readout = { digits: mt.digits, unit: mt.unit, over: false, stale: staleOf(target.name), caption: `${target.name} · ${mt.unit} to spare` };
  } else if (over.length) {
    const mo = magnitude(over[0]);
    readout = { digits: mo.digits, unit: mo.unit, over: true, stale: staleOf(over[0].name), caption: `${over[0].name} · ${mo.unit} over` };
  } else {
    readout = { digits: "------", unit: null, over: false, stale: false, caption: un.length ? "no limit can be evaluated" : "this book has no limits" };
  }
  if (readout.stale) readout.caption += " · stale";

  const lamps: ScopeLamp[] = [
    ...ls.map((l) => ({ name: l.name, state: (l.breached ? "over" : "ahead") as ScopeLamp["state"], pct: pctOf(l), stale: staleOf(l.name) })),
    ...un.map((n) => ({ name: n, state: "unknown" as const, pct: "?", stale: false })),
  ];

  const parts: string[] = [];
  if (target) parts.push(`Nearest limit ${target.name}, ${spoken(target)} to spare.`);
  else if (over.length) parts.push(`Every evaluable limit is breached; the worst, ${over[0].name}, is ${spoken(over[0])} over.`);
  else parts.push(`${readout.caption.charAt(0).toUpperCase()}${readout.caption.slice(1)}.`);
  if (over.length && target) parts.push(`Breached: ${over.map((l) => l.name).join(", ")}.`);
  if (un.length) parts.push(`Cannot be evaluated: ${un.join(", ")}.`);

  return {
    gates,
    target: target ? target.name : null,
    readout,
    rails,
    railsHidden: Math.max(0, over.length - MAX_RAILS),
    unknown: un.slice(),
    lamps,
    summary: parts.join(" "),
  };
}
