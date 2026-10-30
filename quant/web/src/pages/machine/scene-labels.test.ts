import { describe, expect, it } from "vitest";
import { FIXTURE } from "./fixture";
import { LABEL_PRI } from "./labels";
import { layout, RADIUS, toXYZ } from "./layout";
import { labelBook, placeFrame } from "./scene-labels";
import type { LabelSpec } from "./scene-labels";
import type { CentreWeb } from "./scene-geom";
import type { MachineOut, MNode } from "./types";
import { indexGraph, webOf } from "./web";

const gi = indexGraph(FIXTURE);
const { pos, angle } = layout(FIXTURE, gi.byId);
const book = labelBook(gi, pos, angle);
const at = (id: string): number => gi.byId.get(id)!;
const lit = (id: string, g = gi): CentreWeb[] => [{ centre: g.byId.get(id)!, web: webOf(g, g.byId.get(id)!, "web") }];
const by = (specs: LabelSpec[], key: string): LabelSpec | undefined => specs.find((s) => s.key === key);

describe("labelBook, nothing selected", () => {
  const specs = book.active([], 18.85);

  it("offers the book, feeds, products, kill switch, jobs, sections and hours, each at its priority", () => {
    expect(by(specs, "book")?.pri).toBe(LABEL_PRI.book);
    expect(by(specs, "feed:prices")?.pri).toBe(LABEL_PRI.feed);
    expect(by(specs, "p:farm.sweep")?.pri).toBe(LABEL_PRI.product);
    expect(by(specs, "kill")?.pri).toBe(LABEL_PRI.kill);
    expect(by(specs, "j:farm.sweep")?.pri).toBe(LABEL_PRI.job);
    expect(by(specs, "@sect:eq")?.pri).toBe(LABEL_PRI.section);
    expect(by(specs, "@hour:3")?.pri).toBe(LABEL_PRI.hour);
    expect(by(specs, "@session")?.text).toBe("market session");
  });

  it("offers no series, result, strategy or limit", () => {
    for (const id of ["d:NVDA", "c:tsmom@us_equity_indices", "st:exp_a01_spy", "lim:dd-cap"]) expect(by(specs, id)).toBeUndefined();
  });

  it("gives every label a key of its own", () => {
    expect(new Set(specs.map((s) => s.key)).size).toBe(specs.length);
  });

  it("says who a feed comes from, what a product is, and how the kill switch stands, from the payload", () => {
    expect(by(specs, "feed:prices")?.text).toBe("Prices · Yahoo, Alpaca, Stooq");
    expect(by(specs, "p:farm.sweep")?.text).toBe("P4 · Strategy farm");
    expect(by(specs, "kill")?.text).toBe("KILL SWITCH · armed");
    expect(by(specs, "book")?.text).toBe("THE BOOK");
  });

  it("writes the hours on a twelve-hour clock", () => {
    expect(by(specs, "@hour:0")?.text).toBe("12 AM");
    expect(by(specs, "@hour:15")?.text).toBe("3 PM");
  });

  it("names only the sections that hold series, at their series' angle", () => {
    expect(by(specs, "@sect:etf")?.text).toBe("ETFS");
    expect(by(specs, "@sect:fred")?.text).toBe("FRED");
    expect(by(specs, "@sect:ff")).toBeUndefined(); // the fixture has no factors
    const nvda = angle[at("d:NVDA")], aapl = angle[at("d:AAPL")];
    expect(by(specs, "@sect:eq")?.at[0]).toBeCloseTo(toXYZ(RADIUS.daily + 0.55, (nvda + aapl) / 2)[0], 5);
  });

  it("marks a job fainter, and a failed product as failed", () => {
    expect(by(specs, "j:farm.sweep")?.cls).toBe("near");
    expect(by(specs, "feed:prices")?.cls).toBe("big");
    const out = structuredClone(FIXTURE) as MachineOut;
    out.nodes.find((n) => n.id === "p:farm.sweep")!.m.verdict = "FAIL";
    const g = indexGraph(out);
    expect(by(labelBook(g, pos, angle).active([], 0), "p:farm.sweep")?.cls).toBe("fail");
  });

  it("puts the hand's label at the hour it is given", () => {
    expect(by(book.active([], 6), "@now")?.at).toEqual(toXYZ(RADIUS.clock + 0.8, Math.PI / 2, 0.1));
  });
});

describe("labelBook, with a web lit", () => {
  const specs = book.active(lit("d:NVDA"), 18.85);

  it("puts the selection first, then the web", () => {
    expect(by(specs, "d:NVDA")).toMatchObject({ pri: LABEL_PRI.selected, cls: "sel" });
    expect(by(specs, "j:ingest.bars_daily")).toMatchObject({ pri: LABEL_PRI["in-web"], cls: "up" });
    expect(by(specs, "feed:prices")).toMatchObject({ pri: LABEL_PRI["in-web"], cls: "big up" });
    expect(by(specs, "j:vol.forecast_league")).toMatchObject({ pri: LABEL_PRI["in-web"], cls: "down" });
  });

  it("offers a limit, strategy or result in the web, and darkens what stands outside it", () => {
    expect(by(specs, "lim:dd-cap")).toBeUndefined();
    expect(by(specs, "kill")?.cls).toBe("off");
    expect(by(specs, "j:farm.sweep")?.cls).toBe("off");
    expect(by(specs, "@hour:3")?.cls).toBe("hour off");
    expect(by(specs, "@sect:eq")?.cls).toBe("sect off");
    const wider = book.active(lit("p:farm.sweep"), 0);
    expect(by(wider, "c:tsmom@us_equity_indices")?.cls).toBe("down");
    expect(by(wider, "st:farm_tsmom_us_equity_indices")?.cls).toBe("down");
  });

  it("shows a failure in a web as failed, not by its side", () => {
    expect(by(book.active(lit("p:farm.sweep"), 0), "c:xsmom@sectors")?.cls).toBe("fail");
  });

  it("labels a selected node of any kind", () => {
    expect(by(book.active(lit("m:NVDA"), 0), "m:NVDA")?.cls).toBe("sel");
    expect(by(book.active(lit("lim:dd-cap"), 0), "lim:dd-cap")?.cls).toBe("sel");
  });

  it("lights only the side the mode lit", () => {
    const up: CentreWeb[] = [{ centre: at("d:NVDA"), web: webOf(gi, at("d:NVDA"), "reads") }];
    expect(by(book.active(up, 0), "j:vol.forecast_league")?.cls).toBe("off");
  });

  it("leaves out the side tracks' series, which repeat their ticker", () => {
    const web = book.active(lit("j:vol.forecast_league"), 0);
    expect(by(web, "d:NVDA")?.cls).toBe("up");
    expect(by(web, "m:NVDA")).toBeUndefined();
  });

  it("keeps every key unique, across several webs", () => {
    const two = [...lit("d:NVDA"), ...lit("d:SPY")];
    const list = book.active(two, 0);
    expect(new Set(list.map((s) => s.key)).size).toBe(list.length);
    expect(by(list, "d:SPY")?.cls).toBe("sel");
    expect(by(list, "d:NVDA")?.cls).toBe("sel");
  });

  it("drops series labels from a web of 140 nodes or more, keeping the selection's", () => {
    const stub = (i: number): MNode => ({ id: `d:S${i}`, k: "series", l: `S${i}`, n: `S${i}`, ring: "daily", at: { sec: "eq", i }, m: { track: "daily" } });
    const job: MNode = { id: "j:big", k: "job", l: "big", n: "big", ring: "clock", at: { h: 3 }, m: {} };
    const graph = (n: number): MachineOut => ({
      asof: "", nodes: [job, ...Array.from({ length: n }, (_, i) => stub(i))], edges: Array.from({ length: n }, (_, i) => [i + 1, 0, "read"]),
      runs: [], x: { fires: [] }, provenance: [], notes: [],
    });
    for (const [n, shown] of [[139, 139], [140, 0]] as const) {
      const out = graph(n), g = indexGraph(out), l = layout(out, g.byId);
      const list = labelBook(g, l.pos, l.angle).active(lit("j:big", g), 0);
      expect(list.filter((s) => s.key.startsWith("d:"))).toHaveLength(shown);
      expect(by(list, "j:big")?.cls).toBe("sel");
    }
  });
});

describe("placeFrame", () => {
  const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const spec = (key: string, x: number, y: number, pri: number, z = 0, w = 60): LabelSpec => ({ key, text: key, at: [x, y, z], w, h: 13, pri, cls: "c" });

  it("gives each label's anchor in pixels, the middle of its bottom edge", () => {
    expect(placeFrame([spec("a", 0, 0, 1)], IDENTITY, 800, 600)).toEqual([{ key: "a", text: "a", x: 400, y: 300, cls: "c" }]);
  });

  it("drops the lower priority of two that overlap, by their whole rectangles", () => {
    const placed = placeFrame([spec("low", 0, 0, 5), spec("high", 0.1, 0, 1)], IDENTITY, 800, 600); // 40 px apart, 60 wide
    expect(placed.map((p) => p.key)).toEqual(["high"]);
  });

  it("keeps two whose rectangles clear each other", () => {
    expect(placeFrame([spec("a", -0.5, 0, 1), spec("b", 0.5, 0, 1)], IDENTITY, 800, 600)).toHaveLength(2);
  });

  it("hangs a label above its anchor: one just below another clears it", () => {
    const placed = placeFrame([spec("a", 0, 0, 1), spec("b", 0, -0.08, 2)], IDENTITY, 800, 600); // 24 px lower
    expect(placed).toHaveLength(2);
  });

  it("leaves out a label behind the camera", () => {
    const BEHIND = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0];
    expect(placeFrame([spec("back", 0, 0, 1, -1), spec("front", 0, 0, 1, 1)], BEHIND, 800, 600).map((p) => p.key)).toEqual(["front"]);
  });

  it("leaves out a label wholly off the canvas and keeps one partly on it", () => {
    const placed = placeFrame([spec("gone", 2, 0, 1), spec("edge", -1, 0, 1)], IDENTITY, 800, 600);
    expect(placed.map((p) => p.key)).toEqual(["edge"]);
  });

  it("slides a label partly off the side onto the canvas, unless it is wider than the canvas", () => {
    const [left] = placeFrame([spec("left", -1, 0, 1)], IDENTITY, 800, 600);
    expect(left.x).toBe(30);
    const [right] = placeFrame([spec("right", 1, 0, 1)], IDENTITY, 800, 600);
    expect(right.x).toBe(770);
    const [wide] = placeFrame([spec("wide", 0.5, 0, 1, 0, 900)], IDENTITY, 800, 600);
    expect(wide.x).toBe(600);
  });

  it("offers nothing for no labels", () => {
    expect(placeFrame([], IDENTITY, 800, 600)).toEqual([]);
  });

  it("gets a finite anchor for every label of the fixture", () => {
    for (const s of book.active(lit("d:NVDA"), 18.85)) expect(s.at.every(Number.isFinite)).toBe(true);
  });
});
