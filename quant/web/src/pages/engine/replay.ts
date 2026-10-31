/**
 * What the replay on /engine computes from a recording, and nothing it could invent: a
 * node's state is the recording's ran and changed sets, the layout is the recording's ranks
 * and edges, and a reading quotes the recording's own counts and labels.
 */
import { fmtNum } from "../../lib/format";
import type { GraphNode, RecordedEvent, Recording } from "./types";

/**
 * input: a cell the event set. changed: a body ran and its value moved. held: a body ran and
 * its cutoff stopped the change there. idle: reused from the last stabilize.
 */
export type NodeState = "input" | "changed" | "held" | "idle";

export function nodeStates(nodes: GraphNode[], ev: RecordedEvent): Map<string, NodeState> {
  const ran = new Set(ev.ran);
  const changed = new Set(ev.changed);
  return new Map(
    nodes.map((n): [string, NodeState] => {
      if (n.family === "input") return [n.name, changed.has(n.name) ? "input" : "idle"];
      if (!ran.has(n.name)) return [n.name, "idle"];
      return [n.name, changed.has(n.name) ? "changed" : "held"];
    }),
  );
}

/** An edge carried the change: its source moved and its target ran. A held node emits nothing. */
export function edgeLit(states: Map<string, NodeState>, from: string, to: string): boolean {
  const a = states.get(from);
  const b = states.get(to);
  return (a === "input" || a === "changed") && (b === "changed" || b === "held");
}

export interface Tally {
  ran: number;
  changed: number;
  held: string[];
  reused: number;
  named: number;
}

export function tally(rec: Recording, ev: RecordedEvent): Tally {
  const states = nodeStates(rec.graph.nodes, ev);
  const held = rec.graph.nodes.filter((n) => states.get(n.name) === "held").map((n) => n.name);
  const changed = rec.graph.nodes.filter((n) => states.get(n.name) === "changed").length;
  const named = rec.graph.counts.named;
  return { ran: ev.ran.length, changed, held, reused: named - ev.ran.length, named };
}

/** The plain sentence under the figure, from the event's own label and counts. */
export function reading(rec: Recording, ev: RecordedEvent): string {
  const t = tally(rec, ev);
  const first = `${ev.label} ran ${t.ran} of the graph's ${t.named} bodies; the other ${t.reused} were reused.`;
  if (t.held.length === 0) return first;
  const one = t.held.length === 1;
  return `${first} ${t.held.join(", ")} ran and did not move, so ${one ? "its cutoff" : "their cutoffs"} held and nothing downstream ran because of ${one ? "it" : "them"}.`;
}

/** Nanoseconds as a person reads them; the clock's resolution is one microsecond. */
export function fmtNs(ns: number): string {
  if (ns < 1_000) return "< 1 µs";
  if (ns < 1_000_000) return `${Math.round(ns / 1_000)} µs`;
  return `${fmtNum(ns / 1_000_000, ns < 10_000_000 ? 1 : 0)} ms`;
}

// ---------------------------------------------------------------------- layout
const ROW = 13;
const CHAR = 5.7;
const GUTTER = 30;
const PAD = 8;

export interface Placed {
  node: GraphNode;
  x: number;
  y: number;
}

export interface Layout {
  width: number;
  height: number;
  placed: Map<string, Placed>;
}

/** Spread sorted wishes at least ROW apart, keeping their mean where it was. */
function spread(wishes: number[]): number[] {
  const out = [...wishes];
  for (let i = 1; i < out.length; i++) out[i] = Math.max(out[i], out[i - 1] + ROW);
  const shift = wishes.reduce((s, y, i) => s + (y - out[i]), 0) / (wishes.length || 1);
  return out.map((y) => y + shift);
}

/**
 * Columns are Incremental's ranks, so an edge never runs backwards. The inputs stack in the
 * book's order, a lane per name; each later node sits at the mean height of what it reads.
 */
export function layoutGraph(rec: Recording): Layout {
  const { nodes, edges } = rec.graph;
  const lane = new Map(rec.book.positions.map((p, i) => [p.symbol, i]));
  const reads = new Map<string, string[]>();
  for (const [from, to] of edges) reads.set(to, [...(reads.get(to) ?? []), from]);

  const ranks = [...new Set(nodes.map((n) => n.rank))].sort((a, b) => a - b);
  const placed = new Map<string, Placed>();
  let x = PAD;
  let top = 0;
  let bottom = 0;
  for (const rank of ranks) {
    const column = nodes.filter((n) => n.rank === rank);
    const wish = (n: GraphNode, i: number) => {
      const ys = (reads.get(n.name) ?? []).flatMap((r) => placed.get(r)?.y ?? []);
      if (ys.length) return ys.reduce((a, b) => a + b, 0) / ys.length;
      return (lane.get(n.symbol ?? "") ?? lane.size) * 4 * ROW + i * 0.001;
    };
    const ordered = column
      .map((n, i) => ({ n, y: wish(n, i) }))
      .sort((a, b) => a.y - b.y || a.n.name.localeCompare(b.n.name));
    const ys = spread(ordered.map((o) => o.y));
    ordered.forEach((o, i) => {
      placed.set(o.n.name, { node: o.n, x, y: ys[i] });
      top = Math.min(top, ys[i]);
      bottom = Math.max(bottom, ys[i]);
    });
    x += Math.max(...column.map((n) => n.name.length)) * CHAR + GUTTER;
  }
  for (const p of placed.values()) p.y += PAD + ROW / 2 - top;
  return { width: x, height: bottom - top + 2 * PAD + ROW, placed };
}
