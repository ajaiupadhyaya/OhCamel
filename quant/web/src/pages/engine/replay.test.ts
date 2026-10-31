import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { edgeLit, fmtNs, layoutGraph, nodeStates, reading, tally } from "./replay";
import type { Recording } from "./types";

// The committed recording itself: this file reads what the page reads.
const rec = JSON.parse(readFileSync(resolve(__dirname, "../../../../../docs/perf/propagation.json"), "utf8")) as Recording;
const ev = (id: string) => rec.events.find((e) => e.id === id)!;

describe("a node's state is the recording's", () => {
  const tick = ev("tick:SPY");
  const states = nodeStates(rec.graph.nodes, tick);
  it("sets the event's own input cells and no others", () => {
    const set = [...states].filter(([, s]) => s === "input").map(([n]) => n);
    expect(set.sort()).toEqual(["last_tick[SPY]", "price[SPY]"]);
  });
  it("calls a body that ran and did not move held, and one that never ran idle", () => {
    expect(states.get("current_drawdown")).toBe("held");
    expect(states.get("exposure:SPY")).toBe("changed");
    expect(states.get("exposure:QQQ")).toBe("idle");
    expect(states.get("covariance")).toBe("idle");
  });
  it("counts ran, moved, held and reused without a gap", () => {
    const t = tally(rec, tick);
    expect(t.ran).toBe(tick.ran.length);
    expect(t.changed + t.held.length).toBe(t.ran);
    expect(t.ran + t.reused).toBe(t.named);
  });
});

describe("a held node stops the change", () => {
  const states = nodeStates(rec.graph.nodes, ev("tick:SPY"));
  it("lights an edge from a moved node into one that ran", () => {
    expect(edgeLit(states, "price[SPY]", "exposure:SPY")).toBe(true);
  });
  it("lights no edge out of a held node or into a reused one", () => {
    for (const [a, b] of rec.graph.edges) {
      if (states.get(a) === "held" || states.get(b) === "idle") expect(edgeLit(states, a, b)).toBe(false);
    }
  });
});

describe("what the figure's inputs show", () => {
  it("a day's return recomputes covariance and a price does not", () => {
    expect(ev("roll:SPY").ran).toContain("covariance");
    expect(ev("tick:SPY").ran).not.toContain("covariance");
  });
  it("a repeated price stops at the input's cutoff: only the feed's clock runs", () => {
    expect(ev("repeat:SPY").ran.sort()).toEqual(["feed:SPY", "feed_health"]);
  });
});

describe("the reading quotes the recording", () => {
  it("states the event's label and its counts", () => {
    const t = tally(rec, ev("tick:SPY"));
    const text = reading(rec, ev("tick:SPY"));
    expect(text).toContain("A 0.5% tick in SPY");
    expect(text).toContain(`ran ${t.ran} of the graph's ${t.named} bodies`);
  });
  it("names what held, and says nothing of it when nothing did", () => {
    expect(reading(rec, ev("tick:SPY"))).toContain("current_drawdown ran and did not move, so its cutoff held");
    expect(reading(rec, ev("repeat:SPY"))).not.toContain("held");
  });
});

describe("fmtNs", () => {
  it("never claims more than the clock's microsecond", () => {
    expect(fmtNs(0)).toBe("< 1 µs");
    expect(fmtNs(28_000)).toBe("28 µs");
    expect(fmtNs(476_000)).toBe("476 µs");
    expect(fmtNs(5_130_000)).toBe("5.1 ms");
    expect(fmtNs(79_596_000)).toBe("80 ms");
  });
});

describe("the layout", () => {
  const { placed, width, height } = layoutGraph(rec);
  it("places every node inside the figure", () => {
    expect(placed.size).toBe(rec.graph.nodes.length);
    for (const p of placed.values()) {
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThan(width);
      expect(p.y).toBeGreaterThan(0);
      expect(p.y).toBeLessThan(height);
    }
  });
  it("runs every edge left to right, by Incremental's own ranks", () => {
    for (const [a, b] of rec.graph.edges) expect(placed.get(a)!.x).toBeLessThan(placed.get(b)!.x);
  });
  it("keeps two rows of a column apart", () => {
    const byX = new Map<number, number[]>();
    for (const p of placed.values()) byX.set(p.x, [...(byX.get(p.x) ?? []), p.y]);
    for (const ys of byX.values()) {
      ys.sort((a, b) => a - b);
      ys.slice(1).forEach((y, i) => expect(y - ys[i]).toBeGreaterThanOrEqual(12.9));
    }
  });
});
