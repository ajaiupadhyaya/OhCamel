import { describe, expect, it } from "vitest";
import { FIXTURE } from "./fixture";
import { bfs, countKinds, directReaders, drawnShape, edgeInWeb, edgeLevel, indexGraph, nearestFirst, summaryLine, webOf } from "./web";
import type { WebMode } from "./web";
import type { Edge, MachineOut, MNode, NodeKind } from "./types";

const ZERO: Record<NodeKind, number> = { series: 0, feed: 0, job: 0, product: 0, result: 0, strategy: 0, limit: 0, kill: 0, book: 0 };
const counts = (over: Partial<Record<NodeKind, number>>): Record<NodeKind, number> => ({ ...ZERO, ...over });

const gi = indexGraph(FIXTURE);
const at = (id: string): number => {
  const i = gi.byId.get(id);
  if (i === undefined) throw new Error(`no node ${id}`);
  return i;
};
const depthById = (m: Map<number, number>): Record<string, number> => Object.fromEntries([...m].map(([i, d]) => [FIXTURE.nodes[i].id, d]));

/** A graph of `n` stub nodes joined by `pairs` (all "read" edges). */
function graphOf(n: number, pairs: [number, number][]): ReturnType<typeof indexGraph> {
  const nodes: MNode[] = Array.from({ length: n }, (_, i) => ({ id: `n${i}`, k: "series", l: `n${i}`, n: `n${i}`, ring: "daily", at: {}, m: {} }));
  const edges: Edge[] = pairs.map(([a, b]): Edge => [a, b, "read"]);
  return indexGraph({ asof: "", nodes, edges, runs: [], x: { fires: [] }, provenance: [], notes: [] });
}

describe("indexGraph", () => {
  it("keeps the payload and finds each node by its id", () => {
    expect(gi.out).toBe(FIXTURE);
    expect(gi.byId.size).toBe(FIXTURE.nodes.length);
    FIXTURE.nodes.forEach((n, i) => expect(gi.byId.get(n.id)).toBe(i));
  });

  it("lists, for each node, the sources that feed it (up) and the targets it feeds (down), in the direction data flows", () => {
    expect(gi.up).toHaveLength(FIXTURE.nodes.length);
    expect(gi.down).toHaveLength(FIXTURE.nodes.length);
    const nvda = at("d:NVDA");
    expect(gi.up[nvda].map((i) => FIXTURE.nodes[i].id)).toEqual(["j:ingest.bars_daily"]);
    expect(gi.down[nvda].map((i) => FIXTURE.nodes[i].id)).toEqual(["j:vol.forecast_league"]);
    // every edge is in both lists, and the lists hold nothing else
    for (const [a, b] of FIXTURE.edges) {
      expect(gi.down[a]).toContain(b);
      expect(gi.up[b]).toContain(a);
    }
    expect(gi.down.reduce((s, l) => s + l.length, 0)).toBe(FIXTURE.edges.length);
    expect(gi.up.reduce((s, l) => s + l.length, 0)).toBe(FIXTURE.edges.length);
  });

  it("finds an edge by its two ends, keyed 'a>b'", () => {
    expect(gi.edgeAt.size).toBe(FIXTURE.edges.length);
    FIXTURE.edges.forEach(([a, b], ei) => expect(gi.edgeAt.get(`${a}>${b}`)).toBe(ei));
    expect(gi.edgeAt.get(`${at("book")}>${at("d:NVDA")}`)).toBeUndefined();
  });

  it("lists a pair once however many edges join it, and keeps the first edge", () => {
    const out = structuredClone(FIXTURE);
    const [a, b] = out.edges[0];
    out.edges.push([a, b, "pull"]);
    const g = indexGraph(out);
    expect(g.down[a].filter((x) => x === b)).toHaveLength(1);
    expect(g.up[b].filter((x) => x === a)).toHaveLength(1);
    expect(g.edgeAt.get(`${a}>${b}`)).toBe(0);
  });

  it("skips an edge with an end that is not a node, and does not throw", () => {
    const out: MachineOut = structuredClone(FIXTURE);
    const n = out.nodes.length;
    out.edges.push([0, n, "read"], [n + 5, 0, "read"], [-1, 0, "read"], [0, 1.5, "read"], [Number.NaN, 0, "read"]);
    out.edges.push(null as unknown as Edge, [0] as unknown as Edge);
    let g: ReturnType<typeof indexGraph> | undefined;
    expect(() => {
      g = indexGraph(out);
    }).not.toThrow();
    expect(g!.up).toEqual(gi.up);
    expect(g!.down).toEqual(gi.down);
    expect(g!.edgeAt).toEqual(gi.edgeAt);
  });

  it("indexes a payload with no nodes", () => {
    const g = indexGraph({ ...structuredClone(FIXTURE), nodes: [], edges: [[0, 1, "read"]] });
    expect(g.up).toEqual([]);
    expect(g.down).toEqual([]);
    expect(g.byId.size).toBe(0);
    expect(g.edgeAt.size).toBe(0);
  });
});

describe("bfs", () => {
  // 0 -> 1 -> 2 -> 3
  const chain = [[1], [2], [3], []];

  it("gives each node its depth from the start, the start at 0", () => {
    expect(bfs(chain, 0)).toEqual(new Map([[0, 0], [1, 1], [2, 2], [3, 3]]));
    expect(bfs(chain, 2)).toEqual(new Map([[2, 0], [3, 1]]));
  });

  it("gives a node its shortest depth when two paths reach it, though the long one is listed first", () => {
    // 0 -> 4 -> 5 -> 3 is listed before 0 -> 1 -> 3 and 0 -> 2 -> 3
    const diamond = [[4, 1, 2], [3], [3], [], [5], [3]];
    const d = bfs(diamond, 0);
    expect(d.get(3)).toBe(2);
    expect(d.get(5)).toBe(2);
    expect(d.size).toBe(6);
  });

  it("terminates on a cycle, and does not give the start a second depth", () => {
    const cycle = [[1], [2], [0]];
    expect(bfs(cycle, 0)).toEqual(new Map([[0, 0], [1, 1], [2, 2]]));
    const loop = [[0, 1], [1]];
    expect(bfs(loop, 0)).toEqual(new Map([[0, 0], [1, 1]]));
  });

  it("stops at the limit: a longer chain is cut there, seven deep by default", () => {
    const long = Array.from({ length: 12 }, (_, i) => (i < 11 ? [i + 1] : []));
    const d = bfs(long, 0);
    expect(d.size).toBe(8);
    expect(d.get(7)).toBe(7);
    expect(d.has(8)).toBe(false);
    expect(bfs(long, 0, 3).size).toBe(4);
    expect(bfs(long, 0, 3).has(4)).toBe(false);
    expect(bfs(long, 0, 0)).toEqual(new Map([[0, 0]]));
    expect(bfs(long, 0, 20).size).toBe(12);
  });

  it("is empty for a start that is not a node, and walks past a neighbour with no list", () => {
    expect(bfs(chain, 9).size).toBe(0);
    expect(bfs(chain, -1).size).toBe(0);
    expect(bfs(chain, 1.5).size).toBe(0);
    expect(bfs([[5], []], 0)).toEqual(new Map([[0, 0], [5, 1]]));
  });
});

describe("edgeLevel", () => {
  it("is 1 at the selected node and falls as 1 / (1 + 0.55 depth)", () => {
    expect(edgeLevel(0)).toBe(1);
    expect(edgeLevel(1)).toBeCloseTo(1 / 1.55, 12);
    expect(edgeLevel(2)).toBeCloseTo(1 / 2.1, 12);
    expect(edgeLevel(7)).toBeCloseTo(1 / 4.85, 12);
  });

  it("only ever falls with depth", () => {
    for (let d = 0; d < 7; d++) expect(edgeLevel(d + 1)).toBeLessThan(edgeLevel(d));
  });
});

describe("webOf, from d:NVDA on the fixture", () => {
  const nvda = at("d:NVDA");

  it("reads upstream to the feed and feeds downstream to the book, each node at its depth", () => {
    const w = webOf(gi, nvda, "web");
    expect(depthById(w.up)).toEqual({ "j:ingest.bars_daily": 1, "feed:prices": 2 });
    expect(depthById(w.down)).toEqual({
      "j:vol.forecast_league": 1, "p:vol.forecast_league": 2, "j:signals.overlay": 3, "p:signals.overlay": 4, book: 5,
      "j:desk.report": 6, "p:desk.report": 7,
    });
    expect(w.down.has(at("book"))).toBe(true);
  });

  it("does not put the selected node in its own web", () => {
    const w = webOf(gi, nvda, "web");
    expect(w.up.has(nvda)).toBe(false);
    expect(w.down.has(nvda)).toBe(false);
  });

  it("keeps a node on a cycle (the book feeds the report that reads it) out of its own web, too", () => {
    const book = at("book");
    const w = webOf(gi, book, "web");
    expect(w.up.has(book)).toBe(false);
    expect(w.down.has(book)).toBe(false);
    expect(w.down.has(at("j:desk.report"))).toBe(true);
    expect(w.up.has(at("kill"))).toBe(true);
    expect(w.up.has(at("lim:dd-cap"))).toBe(true);
  });

  it("filters by mode: reads is upstream only, feeds downstream only, none nothing", () => {
    const both = webOf(gi, nvda, "web");
    const reads = webOf(gi, nvda, "reads");
    const feeds = webOf(gi, nvda, "feeds");
    const none = webOf(gi, nvda, "none");
    expect(reads.up).toEqual(both.up);
    expect(reads.down.size).toBe(0);
    expect(feeds.down).toEqual(both.down);
    expect(feeds.up.size).toBe(0);
    expect(none.up.size).toBe(0);
    expect(none.down.size).toBe(0);
    expect(both.up.size).toBeGreaterThan(0);
    expect(both.down.size).toBeGreaterThan(0);
  });

  it("is empty for an index that is not a node, in any mode", () => {
    for (const mode of ["web", "reads", "feeds", "none"] as WebMode[]) {
      for (const i of [-1, FIXTURE.nodes.length, 1.5, Number.NaN]) {
        const w = webOf(gi, i, mode);
        expect(w.up.size).toBe(0);
        expect(w.down.size).toBe(0);
      }
    }
  });

  it("stops at seven hops, so a node past seven is not in the web", () => {
    const g = graphOf(12, Array.from({ length: 11 }, (_, i): [number, number] => [i, i + 1]));
    const w = webOf(g, 0, "web");
    expect([...w.down.keys()]).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(w.up.size).toBe(0);
    expect([...webOf(g, 11, "reads").up.keys()]).toEqual([10, 9, 8, 7, 6, 5, 4]);
  });
});

describe("directReaders", () => {
  it("is exactly the jobs with a read edge from the node", () => {
    const nvda = at("d:NVDA");
    const expected = FIXTURE.edges
      .filter(([a, b, kind]) => a === nvda && kind === "read" && FIXTURE.nodes[b].k === "job")
      .map(([, b]) => b);
    expect(expected.length).toBeGreaterThan(0);
    expect(directReaders(gi, nvda)).toEqual(expected);
    expect(directReaders(gi, nvda).map((i) => FIXTURE.nodes[i].id)).toEqual(["j:vol.forecast_league"]);
  });

  it("counts every job that reads a ticker with more than one, and leaves out what is not a job", () => {
    expect(directReaders(gi, at("d:SPY")).map((i) => FIXTURE.nodes[i].id).sort()).toEqual(["j:farm.sweep", "j:regime.hmm", "j:vol.forecast_league"]);
    // d:AAPL's only reader is a limit, a depth-1 node but not a job
    expect(gi.down[at("d:AAPL")].map((i) => FIXTURE.nodes[i].k)).toEqual(["limit"]);
    expect(directReaders(gi, at("d:AAPL"))).toEqual([]);
  });

  it("stays at depth 1: a job two hops downstream is not a direct reader", () => {
    expect(directReaders(gi, at("feed:prices"))).toEqual([at("j:ingest.bars_daily")]);
    expect(directReaders(gi, at("j:ingest.bars_daily"))).toEqual([]);
    expect(directReaders(gi, at("p:farm.sweep")).map((i) => FIXTURE.nodes[i].id)).toEqual(["j:signals.daily"]);
  });

  it("is empty for an index that is not a node", () => {
    expect(directReaders(gi, -1)).toEqual([]);
    expect(directReaders(gi, FIXTURE.nodes.length)).toEqual([]);
  });
});

describe("countKinds", () => {
  it("starts every kind at zero", () => {
    expect(countKinds(gi, [])).toEqual(ZERO);
  });

  it("counts a web by the kind of each node in it", () => {
    const w = webOf(gi, at("d:NVDA"), "web");
    expect(countKinds(gi, w.down.keys())).toEqual(counts({ job: 3, product: 3, book: 1 }));
    expect(countKinds(gi, w.up.keys())).toEqual(counts({ feed: 1, job: 1 }));
  });

  it("counts any iterable of ids, ignoring one that is not a node", () => {
    const ids = [at("d:SPY"), at("d:NVDA"), at("f:VIXCLS"), at("kill"), 9999, -1];
    expect(countKinds(gi, ids)).toEqual(counts({ series: 3, kill: 1 }));
    expect(countKinds(gi, new Set([at("book")]))).toEqual(counts({ book: 1 }));
  });

  it("does not count a node of a kind it does not know, which would turn a count into NaN", () => {
    const out = structuredClone(FIXTURE);
    out.nodes[0].k = "constructor" as NodeKind;
    out.nodes[1].k = "mystery" as NodeKind;
    const c = countKinds(indexGraph(out), [0, 1, 2]);
    expect(c).toEqual(counts({ series: 1 }));
    expect(Object.keys(c).sort()).toEqual(Object.keys(ZERO).sort());
  });
});

describe("summaryLine", () => {
  it("joins the counts with a middle dot, each with its plain word", () => {
    expect(summaryLine(counts({ series: 3, job: 2, product: 1, book: 1 }))).toBe("3 series · 2 jobs · 1 product · 1 book");
  });

  it("names a web of the fixture the way the panel will", () => {
    const w = webOf(gi, at("d:NVDA"), "web");
    expect(summaryLine(countKinds(gi, w.down.keys()))).toBe("3 jobs · 3 products · 1 book");
    expect(summaryLine(countKinds(gi, w.up.keys()))).toBe("1 feed · 1 job");
  });

  it("goes in the order series, feed, job, product, result, strategy, limit, kill, book", () => {
    const all = counts({ series: 2, feed: 2, job: 2, product: 2, result: 2, strategy: 2, limit: 2, kill: 2, book: 2 });
    expect(summaryLine(all).split(" · ").map((s) => s.split(" ").slice(1).join(" "))).toEqual([
      "series", "feeds", "jobs", "products", "results", "strategies", "limits", "kill switches", "books",
    ]);
  });

  it("leaves out a kind with none, and says nothing for an empty web", () => {
    expect(summaryLine(counts({ job: 2, book: 1 }))).toBe("2 jobs · 1 book");
    expect(summaryLine(ZERO)).toBe("");
  });

  it("is singular for exactly one, and series and strategy keep their own forms", () => {
    expect(summaryLine(counts({ series: 1 }))).toBe("1 series");
    expect(summaryLine(counts({ strategy: 1 }))).toBe("1 strategy");
    expect(summaryLine(counts({ strategy: 2 }))).toBe("2 strategies");
    expect(summaryLine(counts({ feed: 1, job: 1, product: 1, result: 1, limit: 1, kill: 1, book: 1 }))).toBe(
      "1 feed · 1 job · 1 product · 1 result · 1 limit · 1 kill switch · 1 book",
    );
  });

  it("groups a large count with fmt.num", () => {
    expect(summaryLine(counts({ series: 1234, job: 12 }))).toBe("1,234 series · 12 jobs");
  });
});

describe("edgeInWeb", () => {
  // 0 -> 1 -> 2 -> 3 -> 4, selecting 2
  const chain = graphOf(5, [[0, 1], [1, 2], [2, 3], [3, 4]]);
  const web = webOf(chain, 2, "web");

  it("puts an edge into the selected node in the up web at depth 0, and an edge out of it in the down web at depth 0", () => {
    expect(edgeInWeb(1, 2, 2, web)).toEqual({ dir: "up", depth: 0 });
    expect(edgeInWeb(2, 3, 2, web)).toEqual({ dir: "down", depth: 0 });
  });

  it("gives an edge further out the depth of its nearer end", () => {
    expect(edgeInWeb(0, 1, 2, web)).toEqual({ dir: "up", depth: 1 });
    expect(edgeInWeb(3, 4, 2, web)).toEqual({ dir: "down", depth: 1 });
  });

  it("leaves out an edge with an end outside the web, and an edge between its two sides", () => {
    const g = graphOf(6, [[0, 1], [1, 2], [2, 3], [0, 3], [4, 5]]);
    const w = webOf(g, 2, "web");
    expect(edgeInWeb(0, 3, 2, w)).toBeNull(); // 0 is upstream of 2 and 3 downstream, but this edge does not pass through 2
    expect(edgeInWeb(4, 5, 2, w)).toBeNull();
  });

  it("lights only the side the mode lit", () => {
    const reads = webOf(chain, 2, "reads");
    expect(edgeInWeb(1, 2, 2, reads)).toEqual({ dir: "up", depth: 0 });
    expect(edgeInWeb(2, 3, 2, reads)).toBeNull();
    const feeds = webOf(chain, 2, "feeds");
    expect(edgeInWeb(1, 2, 2, feeds)).toBeNull();
    expect(edgeInWeb(2, 3, 2, feeds)).toEqual({ dir: "down", depth: 0 });
    const none = webOf(chain, 2, "none");
    expect(edgeInWeb(1, 2, 2, none)).toBeNull();
    expect(edgeInWeb(2, 3, 2, none)).toBeNull();
  });

  it("lets up win on a cycle, where a node is in both maps", () => {
    // 0 -> 1 -> 2 -> 0: selecting 0, nodes 1 and 2 are both upstream and downstream of it
    const cycle = graphOf(3, [[0, 1], [1, 2], [2, 0]]);
    const w = webOf(cycle, 0, "web");
    expect(w.up.has(1) && w.down.has(1)).toBe(true);
    expect(edgeInWeb(1, 2, 0, w)).toEqual({ dir: "up", depth: 1 });
    expect(edgeInWeb(2, 0, 0, w)).toEqual({ dir: "up", depth: 0 });
    // the edge out of the selected node has no end in `up` to start from, so it is down
    expect(edgeInWeb(0, 1, 0, w)).toEqual({ dir: "down", depth: 0 });
  });

  it("matches the web on the fixture: every edge of NVDA's web is reached, and nothing else", () => {
    const nvda = at("d:NVDA");
    const w = webOf(gi, nvda, "web");
    for (const [a, b] of FIXTURE.edges) {
      const hit = edgeInWeb(a, b, nvda, w);
      if (hit?.dir === "up") expect([...w.up.keys(), nvda]).toContain(b);
      if (hit?.dir === "down") expect([...w.down.keys(), nvda]).toContain(a);
    }
    const ingest = at("j:ingest.bars_daily");
    expect(edgeInWeb(ingest, nvda, nvda, w)).toEqual({ dir: "up", depth: 0 });
    expect(edgeInWeb(ingest, at("d:SPY"), nvda, w)).toBeNull();
  });
});

describe("nearestFirst", () => {
  it("orders by hop depth, then everything else before series, then by label", () => {
    const m = new Map([[at("d:SPY"), 1], [at("j:farm.sweep"), 2], [at("d:AAPL"), 1], [at("j:signals.daily"), 1], [at("d:NVDA"), 2]]);
    expect(nearestFirst(gi, m, 48).shown.map((i) => FIXTURE.nodes[i].id)).toEqual(["j:signals.daily", "d:AAPL", "d:SPY", "j:farm.sweep", "d:NVDA"]);
  });

  it("stops at the limit and says how many are left", () => {
    const m = new Map([[at("d:SPY"), 1], [at("d:AAPL"), 1], [at("d:NVDA"), 2]]);
    const { shown, more } = nearestFirst(gi, m, 2);
    expect(shown).toHaveLength(2);
    expect(more).toBe(1);
  });

  it("has nothing left over when all fit", () => {
    expect(nearestFirst(gi, new Map([[at("d:SPY"), 1]]), 48).more).toBe(0);
  });
});

describe("drawnShape", () => {
  const moved = (over: (m: MachineOut) => void): MachineOut => {
    const copy = structuredClone(FIXTURE) as MachineOut;
    over(copy);
    return copy;
  };
  const before = drawnShape(FIXTURE);

  it("ignores what the panels read: a newer read of the same machine needs no new scene", () => {
    const after = moved((m) => {
      m.asof = "2027-01-01T00:00:00Z";
      m.runs = [];
      const farm = m.nodes.find((n) => n.id === "p:farm.sweep")!;
      farm.m.cpu = 1;
      farm.m.finished = "2027-01-01T00:00:00Z";
      farm.m.detail = "NEW";
    });
    expect(drawnShape(after)).toBe(before);
  });

  it("changes with what the scene draws", () => {
    const changes: ((m: MachineOut) => void)[] = [
      (m) => void m.nodes.pop(),
      (m) => void m.nodes.reverse(),
      (m) => void m.edges.pop(),
      (m) => { m.x.fires = []; },
      (m) => { m.nodes.find((n) => n.id === "p:farm.sweep")!.m.verdict = "FAIL"; },
      (m) => { m.nodes.find((n) => n.id === "p:farm.sweep")!.status = "missing"; },
      (m) => { m.nodes.find((n) => n.id === "d:NVDA")!.l = "NV"; },
      (m) => { m.nodes.find((n) => n.id === "kill")!.m.state = "tripped"; },
      (m) => { m.nodes.find((n) => n.id === "feed:prices")!.m.who = "Someone else"; },
      (m) => { m.nodes.find((n) => n.id === "d:NVDA")!.at.i = 99; },
    ];
    for (const change of changes) expect(drawnShape(moved(change))).not.toBe(before);
  });
});
