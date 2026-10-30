/**
 * The webs: select one node and the page lights what it reads (upstream) and what it feeds (downstream), each
 * node at its hop depth, and dims everything else. This module is the pure graph work under that: an index of
 * the payload's edges, a breadth-first walk, and the plain-word counts of a web. No DOM, no three.js.
 */
import { plural } from "./fmt";
import type { Edge, MachineOut, NodeKind } from "./types";

export interface GraphIndex {
  out: MachineOut;
  /** Node id -> its index in `out.nodes`. */
  byId: Map<string, number>;
  /** `up[i]` lists the sources feeding node `i`: the `a` of each edge `[a, i]`. */
  up: number[][];
  /** `down[i]` lists the targets node `i` feeds: the `b` of each edge `[i, b]`. */
  down: number[][];
  /** The index in `out.edges` of the edge from `a` to `b`, keyed `"a>b"`. When two edges join the same pair, the first. */
  edgeAt: Map<string, number>;
}

/** How far a web reaches: the most hops `bfs` takes by default. */
const REACH = 7;

const isNode = (i: number, n: number): boolean => Number.isInteger(i) && i >= 0 && i < n;

/**
 * Index the payload's graph. An edge with an end that is not a node (out of range, not an integer, or not an
 * edge at all) is skipped, so a malformed payload cannot throw. A pair joined by more than one edge is listed
 * once in `up` and `down`; `edgeAt` keeps the first.
 */
export function indexGraph(out: MachineOut): GraphIndex {
  const n = out.nodes.length;
  const up: number[][] = out.nodes.map(() => []);
  const down: number[][] = out.nodes.map(() => []);
  const edgeAt = new Map<string, number>();
  out.edges.forEach((e: Edge | null, ei) => {
    if (!Array.isArray(e)) return;
    const [a, b] = e;
    if (!isNode(a, n) || !isNode(b, n)) return;
    const key = `${a}>${b}`;
    if (edgeAt.has(key)) return;
    edgeAt.set(key, ei);
    down[a].push(b);
    up[b].push(a);
  });
  return { out, byId: new Map(out.nodes.map((node, i) => [node.id, i])), up, down, edgeAt };
}

/**
 * Breadth-first from `start` over `adj` (`adj[i]` lists the nodes one hop from `i`): each node reached, at its
 * shortest depth, the start at 0. It goes at most `limit` hops, so a longer chain stops there. A start that is
 * not a node reaches nothing.
 */
export function bfs(adj: number[][], start: number, limit = REACH): Map<number, number> {
  const depth = new Map<number, number>();
  if (!isNode(start, adj.length)) return depth;
  depth.set(start, 0);
  const queue = [start];
  for (let head = 0; head < queue.length; head++) {
    const u = queue[head];
    const d = depth.get(u)!;
    if (d >= limit) continue;
    for (const v of adj[u] ?? []) {
      if (depth.has(v)) continue;
      depth.set(v, d + 1);
      queue.push(v);
    }
  }
  return depth;
}

/** Which side of the selected node to light: both, what it reads, what it feeds, or neither. */
export type WebMode = "web" | "reads" | "feeds" | "none";

/**
 * The web of node `i`: `up` is what it reads, `down` what it feeds, each node mapped to its hop depth (1 is
 * adjacent). The selected node is in neither map: it is depth 0, and `edgeLevel(0)` is its brightness. "reads"
 * gives `up` only, "feeds" `down` only, "none" nothing; so does an `i` that is not a node.
 */
export function webOf(gi: GraphIndex, i: number, mode: WebMode): { up: Map<number, number>; down: Map<number, number> } {
  const reach = (adj: number[][]): Map<number, number> => {
    const m = bfs(adj, i);
    m.delete(i);
    return m;
  };
  return {
    up: mode === "web" || mode === "reads" ? reach(gi.up) : new Map(),
    down: mode === "web" || mode === "feeds" ? reach(gi.down) : new Map(),
  };
}

/** How bright an edge is at hop depth `depth` of a web: 1 at the selected node, falling away from it. */
export function edgeLevel(depth: number): number {
  return 1 / (1 + 0.55 * depth);
}

/** The jobs that read node `i` directly: its depth-1 targets that are jobs, in edge order. */
export function directReaders(gi: GraphIndex, i: number): number[] {
  return (gi.down[i] ?? []).filter((j) => gi.out.nodes[j].k === "job");
}

/** How many nodes of each kind among `ids`. An id that is not a node, or a node of a kind the page does not know, is not counted. */
export function countKinds(gi: GraphIndex, ids: Iterable<number>): Record<NodeKind, number> {
  const c: Record<NodeKind, number> = { series: 0, feed: 0, job: 0, product: 0, result: 0, strategy: 0, limit: 0, kill: 0, book: 0 };
  for (const i of ids) {
    const node = gi.out.nodes[i];
    if (node && typeof c[node.k] === "number") c[node.k]++;
  }
  return c;
}

/** Each kind's word, singular then plural, in the order a web is described. */
const WORDS: [NodeKind, string, string][] = [
  ["series", "series", "series"],
  ["feed", "feed", "feeds"],
  ["job", "job", "jobs"],
  ["product", "product", "products"],
  ["result", "result", "results"],
  ["strategy", "strategy", "strategies"],
  ["limit", "limit", "limits"],
  ["kill", "kill switch", "kill switches"],
  ["book", "book", "books"],
];

/** A web in plain words: "3 series · 2 jobs · 1 product · 1 book". A kind with none is left out; an empty web is "". */
export function summaryLine(c: Record<NodeKind, number>): string {
  return WORDS.filter(([k]) => c[k] > 0)
    .map(([k, one, many]) => plural(c[k], one, many))
    .join(" · ");
}
