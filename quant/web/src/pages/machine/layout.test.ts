import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { FIXTURE, FIXTURE_BROKEN, FIXTURE_NO_SUMMARY } from "./fixture";
import { circularMean, HEIGHT, hourAngle, layout, RADIUS, RESULT_WIDTH, spread, toXYZ } from "./layout";
import type { EdgeKind, MachineOut, MNode } from "./types";

const TAU = Math.PI * 2;
const VARIANTS: [string, MachineOut][] = [["FIXTURE", FIXTURE], ["FIXTURE_NO_SUMMARY", FIXTURE_NO_SUMMARY], ["FIXTURE_BROKEN", FIXTURE_BROKEN]];

const indexOf = (out: MachineOut): Map<string, number> => new Map(out.nodes.map((n, i) => [n.id, i]));
/** The angle (radians, 0 to 2pi) and position the layout gives the node `id`. */
function place(out: MachineOut, id: string, from = layout(out, indexOf(out))): { angle: number; xyz: number[] } {
  const i = indexOf(out).get(id);
  if (i === undefined) throw new Error(`no node ${id}`);
  return { angle: from.angle[i], xyz: Array.from(from.pos.slice(3 * i, 3 * i + 3)) };
}
/** `a - b` as a turn in [0, 2pi). */
const turn = (a: number, b: number): number => (((a - b) % TAU) + TAU) % TAU;
/** `a - b` the short way round, in [-pi, pi]: near 0 for two angles that agree, however a float32 wrapped one of them. */
const off = (a: number, b: number): number => {
  const d = turn(a, b);
  return d > Math.PI ? d - TAU : d;
};
const clone = (): MachineOut => structuredClone(FIXTURE);
const find = (out: MachineOut, id: string): MNode => {
  const n = out.nodes.find((x) => x.id === id);
  if (!n) throw new Error(`no node ${id}`);
  return n;
};
/** `out` with a node added at the end (so every existing edge index still holds) and the index it got. */
function add(out: MachineOut, node: MNode): number {
  out.nodes.push(node);
  return out.nodes.length - 1;
}
const stub = (id: string, k: MNode["k"], ring: MNode["ring"], at: MNode["at"] = {}): MNode => ({ id, k, l: id, n: id, ring, at, m: {} });
const link = (out: MachineOut, a: string, b: string, kind: EdgeKind): void => {
  out.edges.push([indexOf(out).get(a)!, indexOf(out).get(b)!, kind]);
};

describe("the constants", () => {
  it("narrow ring by ring from the feeds to the book, and rise 0.34 a unit of radius", () => {
    expect(RADIUS).toEqual({
      feeds: 8.4, daily: 7, filings: 6.6, minute: 6.25, options: 5.9, clock: 4.25, products: 3.25, results: 2.35, strategies: 1.45, limits: 0.78, book: 0,
    });
    expect(HEIGHT).toBe(0.34);
  });

  it("gives the wide results their width and leaves the rest to the default", () => {
    expect(RESULT_WIDTH).toEqual({ "p:farm.sweep": 0.95, "p:vol.forecast_league": 0.22, "p:cov.league": 0.36, "p:risk.mc_atlas": 0.22 });
  });
});

describe("hourAngle", () => {
  it("runs the 24-hour clock once round, midnight at angle 0", () => {
    expect(hourAngle(0)).toBe(0);
    expect(hourAngle(6)).toBeCloseTo(Math.PI / 2, 12);
    expect(hourAngle(18)).toBeCloseTo((3 * Math.PI) / 2, 12);
    expect(hourAngle(24)).toBeCloseTo(TAU, 12);
  });
});

describe("toXYZ", () => {
  it("puts angle 0 at the far side (-z) and raises a ring by 0.34 of its radius", () => {
    const [x, y, z] = toXYZ(7, 0);
    expect(x).toBeCloseTo(0, 12);
    expect(y).toBeCloseTo(2.38, 12);
    expect(z).toBeCloseTo(-7, 12);
  });

  it("runs clockwise seen from above: a quarter turn is the +x side", () => {
    const [x, y, z] = toXYZ(7, Math.PI / 2);
    expect(x).toBeCloseTo(7, 12);
    expect(y).toBeCloseTo(2.38, 12);
    expect(z).toBeCloseTo(0, 12);
  });

  it("adds dy to the height only", () => {
    expect(toXYZ(7, 0, 0.1)[1]).toBeCloseTo(2.48, 12);
    expect(toXYZ(7, 0, 0.1)[2]).toBeCloseTo(-7, 12);
  });

  it("puts radius 0 at the origin, with no negative zero", () => {
    expect(toXYZ(0, 0)).toEqual([0, 0, 0]);
    expect(toXYZ(0, 3)).toEqual([0, 0, 0]);
  });
});

describe("circularMean", () => {
  it("takes the short way round: 6.2 and 0.1 meet near 0, not near pi", () => {
    expect(Math.abs(circularMean([6.2, 0.1]))).toBeLessThan(0.15);
  });

  it("is the angle itself for one angle, and the middle of two close ones", () => {
    expect(circularMean([0.5])).toBeCloseTo(0.5, 12);
    expect(circularMean([1, 1.4])).toBeCloseTo(1.2, 12);
  });

  it("is a defined number for none", () => {
    expect(Number.isFinite(circularMean([]))).toBe(true);
  });
});

describe("spread", () => {
  it("lays n angles evenly across width, centred", () => {
    const s = spread(1, 3, 0.4);
    expect(s).toHaveLength(3);
    [0.8, 1, 1.2].forEach((want, i) => expect(s[i]).toBeCloseTo(want, 12));
  });

  it("puts one angle at the centre, and none for none", () => {
    expect(spread(1, 1, 0.4)).toEqual([1]);
    expect(spread(1, 0, 0.4)).toEqual([]);
  });
});

describe("layout on the fixture", () => {
  const got = layout(FIXTURE, indexOf(FIXTURE));
  const angle = (id: string): number => place(FIXTURE, id, got).angle;

  it("gives every node a position (3 floats) and an angle", () => {
    expect(got.pos).toBeInstanceOf(Float32Array);
    expect(got.angle).toBeInstanceOf(Float32Array);
    expect(got.pos).toHaveLength(3 * FIXTURE.nodes.length);
    expect(got.angle).toHaveLength(FIXTURE.nodes.length);
  });

  it("puts each node on its ring at its angle", () => {
    FIXTURE.nodes.forEach((n, i) => {
      const [x, y, z] = toXYZ(RADIUS[n.ring], got.angle[i]);
      expect(Array.from(got.pos.slice(3 * i, 3 * i + 3)), n.id).toEqual([
        expect.closeTo(x, 4), expect.closeTo(y, 4), expect.closeTo(z, 4),
      ]);
    });
  });

  it("puts a job at its hour in New York", () => {
    expect(angle("j:vol.forecast_league")).toBeCloseTo(hourAngle(21.5), 5);
    for (const n of FIXTURE.nodes.filter((x) => x.k === "job")) expect(angle(n.id), n.id).toBeCloseTo(hourAngle(n.at.h!), 5);
  });

  it("puts a product at its job's angle", () => {
    expect(angle("p:farm.sweep")).toBe(angle("j:farm.sweep"));
    for (const n of FIXTURE.nodes.filter((x) => x.k === "product" && x.at.of)) expect(angle(n.id), n.id).toBe(angle(n.at.of!));
  });

  it("puts the research service 0.12 beside the daily signals", () => {
    expect(turn(angle("p:research"), angle("p:signals.daily"))).toBeCloseTo(0.12, 5);
  });

  it("puts the filings, minute and options series at their ticker's angle", () => {
    expect(angle("m:NVDA")).toBe(angle("d:NVDA"));
    expect(angle("o:NVDA")).toBe(angle("d:NVDA"));
    expect(angle("s:NVDA")).toBe(angle("d:NVDA"));
  });

  it("puts the book at the origin", () => {
    expect(place(FIXTURE, "book", got).xyz).toEqual([0, 0, 0]);
  });

  it("spreads a product's results about its angle: the farm's cells across 0.95 rad", () => {
    const farm = angle("p:farm.sweep");
    expect(off(angle("c:tsmom@us_equity_indices"), farm)).toBeCloseTo(-0.475, 5);
    expect(off(angle("c:xsmom@sectors"), farm)).toBeCloseTo(0.475, 5);
  });

  it("spreads the results of any other product 0.3 rad, and of the league and atlas products their own width", () => {
    const widthOf = (product: string): number => {
      const out = clone();
      find(out, "c:tsmom@us_equity_indices").at = { of: product, i: 0, n: 2 };
      find(out, "c:xsmom@sectors").at = { of: product, i: 1, n: 2 };
      return off(place(out, "c:xsmom@sectors").angle, place(out, "c:tsmom@us_equity_indices").angle);
    };
    expect(widthOf("p:regime.hmm")).toBeCloseTo(0.3, 5);
    expect(widthOf("p:vol.forecast_league")).toBeCloseTo(0.22, 5);
    expect(widthOf("p:farm.sweep")).toBeCloseTo(0.95, 5);
  });

  it("spaces the strategies evenly round from 19:30", () => {
    expect(angle("st:exp_a01_spy")).toBeCloseTo(hourAngle(19.5), 5);
    expect(turn(angle("st:farm_tsmom_us_equity_indices"), hourAngle(19.5))).toBeCloseTo(Math.PI, 5);
  });

  it("spaces the limits, and the kill switch as one more, evenly round from 01:00", () => {
    expect(angle("lim:aapl-cap")).toBeCloseTo(hourAngle(1), 5);
    expect(turn(angle("lim:dd-cap"), hourAngle(1))).toBeCloseTo(TAU / 3, 5);
    expect(turn(angle("kill"), hourAngle(1))).toBeCloseTo((2 * TAU) / 3, 5);
  });

  it("puts a feed at the circular mean of the series its job writes", () => {
    const written = ["d:SPY", "d:NVDA", "d:AAPL"].map(angle);
    const want = Math.atan2(written.reduce((s, a) => s + Math.sin(a), 0), written.reduce((s, a) => s + Math.cos(a), 0));
    expect(off(angle("feed:prices"), want)).toBeCloseTo(0, 4);
  });

  it("lays the daily ring's sections end to end from 00:36, six slots apart", () => {
    // etf 1 (SPY), eq 2 (NVDA, AAPL), fred 1 (VIXCLS), ff 0, 13f 0: 4 slots and five 6-slot gaps make 34
    const at = (slot: number): number => hourAngle(0.6) + ((slot + 0.5) / 34) * TAU;
    expect(off(angle("d:SPY"), at(0))).toBeCloseTo(0, 5);
    expect(off(angle("d:NVDA"), at(7))).toBeCloseTo(0, 5);
    expect(off(angle("d:AAPL"), at(8))).toBeCloseTo(0, 5);
    expect(off(angle("f:VIXCLS"), at(15))).toBeCloseTo(0, 5);
  });
});

describe.each(VARIANTS)("layout on %s", (_name, out) => {
  const got = layout(out, indexOf(out));

  it("is finite everywhere, with each angle within one turn", () => {
    expect(Array.from(got.pos).every(Number.isFinite)).toBe(true);
    expect(Array.from(got.angle).every(Number.isFinite)).toBe(true);
    expect(Array.from(got.angle).every((a) => a >= 0 && a <= TAU + 1e-5)).toBe(true);
  });

  it("is the same twice, and leaves the graph as it was", () => {
    const before = structuredClone(out);
    const again = layout(out, indexOf(out));
    expect(Array.from(again.pos)).toEqual(Array.from(got.pos));
    expect(Array.from(again.angle)).toEqual(Array.from(got.angle));
    expect(out).toEqual(before);
  });
});

describe("layout when the server sends less than it should", () => {
  it("sizes a daily section by its highest index, so a gap in the indices does not move the sections after it", () => {
    const out = clone();
    find(out, "d:AAPL").at = { sec: "eq", i: 2 }; // eq holds i 0 and 2: three slots, one of them empty
    // etf 1 + eq 3 + fred 1, and five gaps: 35
    const at = (slot: number): number => hourAngle(0.6) + ((slot + 0.5) / 35) * TAU;
    expect(off(place(out, "d:NVDA").angle, at(7))).toBeCloseTo(0, 5);
    expect(off(place(out, "d:AAPL").angle, at(9))).toBeCloseTo(0, 5);
    expect(off(place(out, "f:VIXCLS").angle, at(7 + 3 + 6))).toBeCloseTo(0, 5);
  });

  it("gives a product with no clock entry (at: {}) a defined position, at angle 0", () => {
    const out = clone();
    find(out, "p:regime.hmm").at = {};
    const p = place(out, "p:regime.hmm");
    expect(p.angle).toBe(0);
    expect(p.xyz.every(Number.isFinite)).toBe(true);
    expect(p.xyz[0]).toBeCloseTo(0, 5);
    expect(p.xyz[2]).toBeCloseTo(-RADIUS.products, 5);
  });

  it("puts a result of a product with no clock entry about angle 0 too", () => {
    const out = clone();
    find(out, "p:farm.sweep").at = {};
    expect(off(place(out, "c:xsmom@sectors").angle, 0)).toBeCloseTo(0.475, 5);
  });

  it("puts a node beside or of something that is not in the graph at a defined angle", () => {
    const out = clone();
    find(out, "m:NVDA").at = { of: "d:ZZZZ" };
    find(out, "p:research").at = { beside: "p:gone" };
    find(out, "c:xsmom@sectors").at = { of: "p:gone", i: 1, n: 2 };
    for (const id of ["m:NVDA", "p:research", "c:xsmom@sectors"]) {
      const p = place(out, id);
      expect(Number.isFinite(p.angle), id).toBe(true);
      expect(p.xyz.every(Number.isFinite), id).toBe(true);
    }
  });

  it("keeps a node whose index or count is missing, or whose index is out of range, finite", () => {
    const out = clone();
    find(out, "st:exp_a01_spy").at = {};
    find(out, "lim:aapl-cap").at = { i: 7, n: 3 };
    find(out, "c:tsmom@us_equity_indices").at = { of: "p:farm.sweep" };
    find(out, "j:regime.hmm").at = {};
    for (const n of out.nodes) {
      const p = place(out, n.id);
      expect(Number.isFinite(p.angle), n.id).toBe(true);
      expect(p.xyz.every(Number.isFinite), n.id).toBe(true);
    }
  });

  it("keeps finite what the wire types forbid: an hour that is not a number, a section or ring that does not exist", () => {
    const out = clone();
    find(out, "j:farm.sweep").at = { h: "late" as unknown as number };
    find(out, "d:NVDA").at = { sec: "moon" as unknown as "eq", i: 0 };
    find(out, "d:SPY").ring = "outer" as unknown as MNode["ring"];
    for (const id of ["j:farm.sweep", "p:farm.sweep", "d:NVDA", "d:SPY"]) {
      const p = place(out, id);
      expect(Number.isFinite(p.angle), id).toBe(true);
      expect(p.xyz.every(Number.isFinite), id).toBe(true);
    }
  });

  it("survives a node that is placed by itself or by a node placed by it", () => {
    const out = clone();
    find(out, "m:NVDA").at = { of: "m:NVDA" };
    find(out, "s:NVDA").at = { of: "o:NVDA" };
    find(out, "o:NVDA").at = { of: "s:NVDA" };
    for (const id of ["m:NVDA", "s:NVDA", "o:NVDA"]) expect(Number.isFinite(place(out, id).angle), id).toBe(true);
  });
});

describe("feeds in layout", () => {
  const SERIES_ANGLE = (out: MachineOut, ids: string[]): number => {
    const a = ids.map((id) => place(out, id).angle);
    return Math.atan2(a.reduce((s, x) => s + Math.sin(x), 0), a.reduce((s, x) => s + Math.cos(x), 0));
  };

  it("nudges the filings feed 0.18 rad off the series its job writes", () => {
    const out = clone();
    add(out, stub("feed:sec", "feed", "feeds"));
    add(out, stub("j:ingest.sec_facts", "job", "clock", { h: 19 }));
    link(out, "feed:sec", "j:ingest.sec_facts", "pull");
    link(out, "j:ingest.sec_facts", "s:NVDA", "write");
    expect(turn(place(out, "feed:sec").angle, place(out, "s:NVDA").angle)).toBeCloseTo(0.18, 5);
  });

  it("sits the rates feed at its warehouse job's series, though its second job writes nothing", () => {
    const out = clone();
    add(out, stub("feed:fred", "feed", "feeds"));
    add(out, stub("j:ingest.fred_warehouse", "job", "clock", { h: 18 }));
    add(out, stub("j:ingest.fred", "job", "clock", { h: 6 }));
    link(out, "feed:fred", "j:ingest.fred_warehouse", "pull");
    link(out, "feed:fred", "j:ingest.fred", "pull");
    link(out, "j:ingest.fred_warehouse", "f:VIXCLS", "write");
    expect(off(place(out, "feed:fred").angle, place(out, "f:VIXCLS").angle)).toBeCloseTo(0, 5);
  });

  it("counts a series once even when two of its jobs write it", () => {
    const out = clone();
    add(out, stub("feed:two", "feed", "feeds"));
    add(out, stub("j:a", "job", "clock", { h: 1 }));
    add(out, stub("j:b", "job", "clock", { h: 2 }));
    link(out, "feed:two", "j:a", "pull");
    link(out, "feed:two", "j:b", "pull");
    link(out, "j:a", "d:SPY", "write");
    link(out, "j:a", "d:NVDA", "write");
    link(out, "j:b", "d:SPY", "write");
    expect(off(place(out, "feed:two").angle, SERIES_ANGLE(out, ["d:SPY", "d:NVDA"]))).toBeCloseTo(0, 5);
  });

  it("puts a feed whose jobs write nothing at its jobs, and a feed with no job at a defined angle", () => {
    const out = clone();
    add(out, stub("feed:idle", "feed", "feeds"));
    add(out, stub("feed:orphan", "feed", "feeds"));
    add(out, stub("j:quiet", "job", "clock", { h: 6 }));
    link(out, "feed:idle", "j:quiet", "pull");
    expect(off(place(out, "feed:idle").angle, hourAngle(6))).toBeCloseTo(0, 5);
    const orphan = place(out, "feed:orphan");
    expect(Number.isFinite(orphan.angle)).toBe(true);
    expect(orphan.xyz.every(Number.isFinite)).toBe(true);
  });

  it("puts a feed on the feeds ring", () => {
    const { xyz } = place(FIXTURE, "feed:prices");
    expect(Math.hypot(xyz[0], xyz[2])).toBeCloseTo(RADIUS.feeds, 4);
    expect(xyz[1]).toBeCloseTo(HEIGHT * RADIUS.feeds, 4);
  });
});

describe("the layout module", () => {
  const src = readFileSync(new URL("./layout.ts", import.meta.url), "utf8");

  it("is pure: it imports no package (so not three), and touches no DOM", () => {
    const imports = [...src.matchAll(/^import .* from "(.*)";$/gm)].map((m) => m[1]);
    expect(imports).toContain("./types");
    expect(imports.filter((from) => !from.startsWith("./"))).toEqual([]);
    expect(src).not.toMatch(/\b(document|window|navigator|requestAnimationFrame)\b/);
  });
});
