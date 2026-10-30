import { describe, expect, it } from "vitest";
import { FIXTURE } from "./fixture";
import { search } from "./search";
import type { MachineOut, MNode } from "./types";
import { indexGraph } from "./web";

const gi = indexGraph(FIXTURE);
const ids = (g: ReturnType<typeof indexGraph>, q: string, limit?: number): string[] => search(g, q, limit).map((i) => g.out.nodes[i].id);

/** The fixture with extra nodes appended (no edges: search reads nodes only). */
function withNodes(...extra: (Pick<MNode, "id" | "k" | "l" | "n" | "ring"> & { track?: string })[]): ReturnType<typeof indexGraph> {
  const out: MachineOut = structuredClone(FIXTURE);
  out.nodes.push(...extra.map(({ track, ...x }): MNode => ({ ...x, at: {}, m: x.k === "series" ? { track: track ?? "daily" } : {} })));
  return indexGraph(out);
}

describe("search: what matches", () => {
  it("matches a case-insensitive substring of the id, the label, the name or the kind", () => {
    expect(ids(gi, "forecast_league")).toContain("j:vol.forecast_league");     // id
    expect(ids(gi, "STRATEGY FARM")).toContain("j:farm.sweep");                // name, in capitals
    expect(ids(gi, "kill switch")).toEqual(["kill"]);                          // label
    expect(ids(gi, "exp a01")).toEqual(["st:exp_a01_spy"]);                    // label
    expect(ids(gi, "Alpaca")).toEqual(["book", "feed:prices"]);                // name (the book first)
    // the kind: every limit is found by "limit"
    expect(ids(gi, "limit")).toEqual(expect.arrayContaining(["lim:aapl-cap", "lim:dd-cap"]));
    expect(ids(gi, "strategy")).toEqual(expect.arrayContaining(["st:exp_a01_spy", "st:farm_tsmom_us_equity_indices"]));
  });

  it("finds nothing for text no node carries", () => {
    expect(search(gi, "zzzz-no-such-thing")).toEqual([]);
  });

  it("returns node indices, each once", () => {
    const hits = search(gi, "a");
    expect(hits.length).toBeGreaterThan(1);
    expect(new Set(hits).size).toBe(hits.length);
    hits.forEach((i) => expect(FIXTURE.nodes[i]).toBeDefined());
  });

  it("ignores spaces around the text", () => {
    expect(ids(gi, "  kill switch \t")).toEqual(["kill"]);
  });
});

describe("search: ranking", () => {
  it("ranks the daily series before the other tracks of the same ticker", () => {
    expect(ids(gi, "nvda")[0]).toBe("d:NVDA");
    const hits = ids(gi, "nvda");
    expect(hits.indexOf("d:NVDA")).toBeLessThan(hits.indexOf("m:NVDA"));
    expect(hits.indexOf("d:NVDA")).toBeLessThan(hits.indexOf("o:NVDA"));
    expect(hits.indexOf("d:NVDA")).toBeLessThan(hits.indexOf("s:NVDA"));
  });

  it("ranks a daily series before every other track whatever the payload's order", () => {
    const g = withNodes(
      { id: "m:ZZ", k: "series", l: "ZZ", n: "ZZ minute", ring: "minute", track: "minute" },
      { id: "f:ZZ", k: "series", l: "ZZ", n: "ZZ macro", ring: "daily", track: "macro" },
      { id: "s:ZZ", k: "series", l: "ZZ", n: "ZZ filings", ring: "filings", track: "filings" },
      { id: "d:ZZ", k: "series", l: "ZZ", n: "ZZ daily", ring: "daily", track: "daily" },
    );
    expect(ids(g, "zz")).toEqual(["d:ZZ", "m:ZZ", "f:ZZ", "s:ZZ"]);   // the others keep the payload's order
  });

  it("ranks the farm's product before its job, its strategy, and its cells", () => {
    expect(ids(gi, "farm").slice(0, 3)).toEqual(["p:farm.sweep", "j:farm.sweep", "st:farm_tsmom_us_equity_indices"]);
    // a cell that carries the word comes after the product
    const g = withNodes({ id: "c:farm_tsmom@sectors", k: "result", l: "farm_tsmom@sectors", n: "farm tsmom on sectors", ring: "results" });
    const hits = ids(g, "farm");
    expect(hits.indexOf("p:farm.sweep")).toBeLessThan(hits.indexOf("c:farm_tsmom@sectors"));
    expect(hits.indexOf("j:farm.sweep")).toBeLessThan(hits.indexOf("c:farm_tsmom@sectors"));
  });

  it("ranks the book first for 'book', ahead of anything else that says book", () => {
    expect(ids(gi, "book")[0]).toBe("book");
    const g = withNodes(
      { id: "d:BOOKS", k: "series", l: "BOOKS", n: "Books-A-Million", ring: "daily" },
      { id: "feed:order-book", k: "feed", l: "Order book", n: "The order book feed", ring: "feeds" },
    );
    expect(ids(g, "book")[0]).toBe("book");
  });

  it("ranks kinds in the order book, product, feed, job, strategy, result, then series", () => {
    const g = withNodes(
      { id: "d:ZZ", k: "series", l: "zz series", n: "zz", ring: "daily" },
      { id: "c:zz", k: "result", l: "zz result", n: "zz", ring: "results" },
      { id: "st:zz", k: "strategy", l: "zz strategy", n: "zz", ring: "strategies" },
      { id: "j:zz", k: "job", l: "zz job", n: "zz", ring: "clock" },
      { id: "feed:zz", k: "feed", l: "zz feed", n: "zz", ring: "feeds" },
      { id: "p:zz", k: "product", l: "zz product", n: "zz", ring: "products" },
      { id: "book:zz", k: "book", l: "zz book", n: "zz", ring: "book" },
    );
    expect(ids(g, "zz")).toEqual(["book:zz", "p:zz", "feed:zz", "j:zz", "st:zz", "c:zz", "d:ZZ"]);
  });

  it("ranks the limits and the kill switch after the results and before the series", () => {
    const g = withNodes(
      { id: "d:QQ", k: "series", l: "qq series", n: "qq", ring: "daily" },
      { id: "lim:qq", k: "limit", l: "qq limit", n: "qq", ring: "limits" },
      { id: "c:qq", k: "result", l: "qq result", n: "qq", ring: "results" },
      { id: "kill:qq", k: "kill", l: "qq kill", n: "qq", ring: "limits" },
    );
    expect(ids(g, "qq")).toEqual(["c:qq", "lim:qq", "kill:qq", "d:QQ"]);
  });

  it("ranks an exact label before everything else, whatever the kind", () => {
    const g = withNodes(
      { id: "p:abc", k: "product", l: "ABC product", n: "abc", ring: "products" },
      { id: "d:ABC", k: "series", l: "abc", n: "ABC Corp", ring: "daily" },
    );
    expect(ids(g, "abc")).toEqual(["d:ABC", "p:abc"]);
    expect(ids(g, "  ABC  ")).toEqual(["d:ABC", "p:abc"]);
  });

  it("ranks the shorter label first among the same kind", () => {
    const g = withNodes(
      { id: "j:yy.long", k: "job", l: "yy the long job", n: "yy", ring: "clock" },
      { id: "j:yy.short", k: "job", l: "yy job", n: "yy", ring: "clock" },
    );
    expect(ids(g, "yy")).toEqual(["j:yy.short", "j:yy.long"]);
  });

  it("is the same on every call (the order never depends on what was searched before)", () => {
    expect(search(gi, "a")).toEqual(search(gi, "a"));
    expect(search(gi, "nvda")).toEqual(search(gi, "nvda"));
  });
});

describe("search: the limit", () => {
  it("returns at most 12 by default", () => {
    expect(search(gi, "a").length).toBeLessThanOrEqual(12);
    expect(search(gi, "a")).toHaveLength(12);
  });

  it("returns at most the limit asked for, the best first", () => {
    const all = search(gi, "a", 1000);
    expect(all.length).toBeGreaterThan(12);
    expect(search(gi, "a", 3)).toEqual(all.slice(0, 3));
    expect(search(gi, "a", 1)).toEqual(all.slice(0, 1));
  });

  it("returns nothing for a limit of zero, a negative limit or one that is not a number", () => {
    expect(search(gi, "a", 0)).toEqual([]);
    expect(search(gi, "a", -1)).toEqual([]);
    expect(search(gi, "a", Number.NaN)).toEqual([]);
  });
});

describe("search: any input is safe", () => {
  it.each([["empty", ""], ["spaces", "   "], ["tabs and newlines", " \t\n "]])("returns nothing for %s", (_name, q) => {
    expect(search(gi, q)).toEqual([]);
  });

  it("returns nothing for a very long string", () => {
    expect(search(gi, "a".repeat(10_000))).toEqual([]);
    expect(search(gi, "nvda ".repeat(5_000))).toEqual([]);
    // text longer than any name is not tried, even when a node carries it whole
    const name = "a very long name ".repeat(20);
    const g = withNodes({ id: "j:long", k: "job", l: name.trim(), n: name.trim(), ring: "clock" });
    expect(ids(g, name.slice(0, 50))).toEqual(["j:long"]);
    expect(search(g, name.trim())).toEqual([]);
  });

  it.each(["(", ")", "[", "]", "{", "}", "*", "+", "?", "\\", "|", "^", "$", ".*", "(.*)", "[a-", "(?<=x)", "\\d+", "a{2,1}", "%", "<img src=x onerror=alert(1)>", "\u0000", "\ud800"])(
    "does not throw on %j",
    (q) => {
      expect(() => search(gi, q)).not.toThrow();
    },
  );

  it("matches regular-expression characters literally", () => {
    expect(search(gi, ".*")).toEqual([]);
    expect(search(gi, ".")).not.toEqual([]);
    // "." is a dot, not 'any character': "farm.sweep" is not "farmXsweep"
    const g = withNodes({ id: "j:farmXsweep", k: "job", l: "farmXsweep", n: "decoy", ring: "clock" });
    expect(ids(g, "farm.sweep")).not.toContain("j:farmXsweep");
    expect(ids(g, "farm.sweep")).toEqual(["p:farm.sweep", "j:farm.sweep"]);
    // "[" and "(" and "+" are found where a node carries them
    const h = withNodes({ id: "x:[a](b)+c", k: "series", l: "[a](b)+c", n: "brackets", ring: "daily" });
    expect(ids(h, "[a](b)+c")).toEqual(["x:[a](b)+c"]);
    expect(ids(h, "(b)+")).toEqual(["x:[a](b)+c"]);
    expect(ids(h, "a+")).toEqual([]);
  });

  it("is safe on a node whose fields are not text", () => {
    const out: MachineOut = structuredClone(FIXTURE);
    (out.nodes[0] as unknown as Record<string, unknown>).l = null;
    (out.nodes[1] as unknown as Record<string, unknown>).n = 7;
    (out.nodes[2] as unknown as Record<string, unknown>).id = undefined;
    const g = indexGraph(out);
    expect(() => search(g, "nvda")).not.toThrow();
    expect(() => search(g, "spy")).not.toThrow();
  });

  it("is safe on a graph with no nodes, and on a query that is not text", () => {
    const empty = indexGraph({ asof: "", nodes: [], edges: [], runs: [], x: { fires: [] }, provenance: [], notes: [] });
    expect(search(empty, "a")).toEqual([]);
    expect(search(gi, undefined as unknown as string)).toEqual([]);
    expect(search(gi, 42 as unknown as string)).toEqual([]);
  });

  it("does not change the graph", () => {
    const before = structuredClone(FIXTURE);
    search(gi, "a", 100);
    expect(gi.out).toEqual(before);
  });
});
