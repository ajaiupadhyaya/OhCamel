import { describe, expect, it } from "vitest";
import { FIXTURE } from "./fixture";
import { HEIGHT, hourAngle, RADIUS, toXYZ } from "./layout";
import {
  ease, edgeStyle, fibreControl, FLOW, fireTickVerts, frameFit, glowOf, hourTickVerts, isSideTrack, LOOM, nearestNode, nodeShot, nodeSize, nodeTone, OUTSIDE, OVERVIEW,
  projectNodes, radiusOf, ringVerts, sameView, SEGMENTS, sessionVerts, shortTheta, shotAt, toScreen, writeFibre, YOURS, zoomDistance,
} from "./scene-geom";
import type { CentreWeb, Vec3 } from "./scene-geom";
import type { Edge, MNode, NodeKind, Ring } from "./types";
import { edgeLevel, indexGraph, webOf } from "./web";

const node = (k: NodeKind, m: MNode["m"] = {}, extra: Partial<MNode> = {}): MNode => ({ id: "x", k, l: "x", n: "x", ring: "daily", at: {}, m, ...extra });

/** The radius and angle of a point in the x-z plane, the way layout places it. */
const polar = (p: Vec3): { r: number; a: number } => ({ r: Math.hypot(p[0], p[2]), a: Math.atan2(p[0], -p[2]) });

/** Four-vertex runs of [x, y, z, x, y, z] as pairs of points. */
const pairs = (v: number[]): [Vec3, Vec3][] => {
  const out: [Vec3, Vec3][] = [];
  for (let i = 0; i < v.length; i += 6) out.push([[v[i], v[i + 1], v[i + 2]], [v[i + 3], v[i + 4], v[i + 5]]]);
  return out;
};

describe("fibreControl", () => {
  it("sits 72% of the angular way to the target, at the mean radius", () => {
    const c = fibreControl(8, 0.2, 4, 1.2);
    expect(polar(c).a).toBeCloseTo(0.2 + 0.72, 10);
    expect(polar(c).r).toBeCloseTo(6, 10);
  });

  it("is raised above the cone to max(hA, hB) + 0.25 + 0.12 |ΔR|", () => {
    const c = fibreControl(8, 0.2, 4, 1.2);
    expect(c[1]).toBeCloseTo(HEIGHT * 8 + 0.25 + 0.12 * 4, 10);
    // the same whichever end is the higher
    expect(fibreControl(4, 1.2, 8, 0.2)[1]).toBeCloseTo(c[1], 10);
  });

  it("goes the short way round", () => {
    const c = fibreControl(5, 6.2, 5, 0.1); // 6.2 to 0.1 crosses zero, a step of about +0.18
    const step = 0.1 + Math.PI * 2 - 6.2;
    expect(polar(c).a).toBeCloseTo(6.2 + 0.72 * step - Math.PI * 2, 10);
  });

  it("gives the book, at the centre, the other end's angle", () => {
    expect(polar(fibreControl(0, 3, 4, 1)).a).toBeCloseTo(1, 10);
    expect(polar(fibreControl(4, 1, 0, 3)).a).toBeCloseTo(1, 10);
  });
});

describe("writeFibre", () => {
  const a: Vec3 = [1, 2, 3], c: Vec3 = [5, 7, -1], b: Vec3 = [-2, 0, 4];
  const out = new Float32Array((SEGMENTS + 1) * 3 + 6);
  writeFibre(out, 1, a, c, b); // from point 1, so point 0 stays untouched
  const point = (s: number): number[] => [...out.slice(3 * (1 + s), 3 * (2 + s))];

  it("draws 16 segments, starting at the source and ending at the target", () => {
    expect(SEGMENTS).toBe(16);
    a.forEach((x, d) => expect(point(0)[d]).toBeCloseTo(x, 5));
    b.forEach((x, d) => expect(point(SEGMENTS)[d]).toBeCloseTo(x, 5));
  });

  it("is the quadratic through its control point", () => {
    const mid = point(SEGMENTS / 2);
    [0, 1, 2].forEach((d) => expect(mid[d]).toBeCloseTo(0.25 * a[d] + 0.5 * c[d] + 0.25 * b[d], 5));
  });

  it("leaves the rest of the buffer alone", () => {
    expect([...out.slice(0, 3)]).toEqual([0, 0, 0]);
  });
});

describe("edgeStyle", () => {
  const g = indexGraph({ asof: "", nodes: Array.from({ length: 6 }, (_, i): MNode => ({ ...node("series"), id: `n${i}` })), edges: ([[0, 1], [1, 2], [2, 3], [4, 5]] as [number, number][]).map(([a, b]): Edge => [a, b, "read"]), runs: [], x: { fires: [] }, provenance: [], notes: [] });
  const of = (centre: number): CentreWeb => ({ centre, web: webOf(g, centre, "web") });
  const none = new Set<number>();

  it("draws the loom at 0.07 with no web lit", () => {
    expect(edgeStyle(0, 1, [], none)).toEqual({ level: LOOM, flow: FLOW.none });
    expect(LOOM).toBe(0.07);
  });

  it("drops what is outside a web to 0.014", () => {
    expect(edgeStyle(4, 5, [of(2)], none)).toEqual({ level: OUTSIDE, flow: FLOW.none });
    expect(OUTSIDE).toBe(0.014);
  });

  it("gives a web's edges edgeLevel(depth): reads upstream, feeds downstream", () => {
    expect(edgeStyle(1, 2, [of(2)], none)).toEqual({ level: edgeLevel(0), flow: FLOW.up });
    expect(edgeStyle(0, 1, [of(2)], none)).toEqual({ level: edgeLevel(1), flow: FLOW.up });
    expect(edgeStyle(2, 3, [of(2)], none)).toEqual({ level: edgeLevel(0), flow: FLOW.down });
  });

  it("takes the nearest of several webs", () => {
    expect(edgeStyle(1, 2, [of(3), of(2)], none)).toEqual({ level: edgeLevel(0), flow: FLOW.up });
  });

  it("marks an edge into a running job, keeping its level, unless a web already lit it", () => {
    const running = new Set([1, 5]);
    expect(edgeStyle(0, 1, [], running)).toEqual({ level: LOOM, flow: FLOW.run });
    expect(edgeStyle(4, 5, [of(2)], running)).toEqual({ level: OUTSIDE, flow: FLOW.run });
    expect(edgeStyle(0, 1, [of(2)], running)).toEqual({ level: edgeLevel(1), flow: FLOW.up });
    expect(edgeStyle(1, 2, [], running)).toEqual({ level: LOOM, flow: FLOW.none }); // out of a running job is not reading into it
  });
});

describe("glowOf", () => {
  const g = indexGraph(FIXTURE);
  const nvda = g.byId.get("d:NVDA")!, ingest = g.byId.get("j:ingest.bars_daily")!, feed = g.byId.get("feed:prices")!, kill = g.byId.get("kill")!;
  const webs: CentreWeb[] = [{ centre: nvda, web: webOf(g, nvda, "web") }];

  it("is 0.75 with no web lit", () => {
    expect(glowOf(nvda, [], 1.8)).toBe(0.75);
  });

  it("is the centre's glow at the centre, falls with depth inside the web, and is 0 outside it", () => {
    expect(glowOf(nvda, webs, 1.8)).toBe(1.8);
    expect(glowOf(ingest, webs, 1.8)).toBeCloseTo(1.15 - 0.09, 10);
    expect(glowOf(feed, webs, 1.8)).toBeCloseTo(1.15 - 0.18, 10);
    expect(glowOf(kill, webs, 1.8)).toBe(0);
  });
});

describe("nodeSize", () => {
  it("sizes each kind as the spec does", () => {
    const sizes = Object.fromEntries((["job", "product", "result", "strategy", "feed", "book", "limit", "kill"] as NodeKind[]).map((k) => [k, nodeSize(node(k))]));
    expect(sizes).toEqual({ job: 6, product: 9, result: 5.5, strategy: 5.5, feed: 11, book: 18, limit: 5.5, kill: 5.5 });
  });

  it("draws daily, macro, factor and 13F series at 2.4 and the side tracks at 1.8", () => {
    for (const track of ["daily", "macro", "factors", "13f"]) expect(nodeSize(node("series", { track }))).toBe(2.4);
    for (const track of ["filings", "minute", "options"]) {
      expect(nodeSize(node("series", { track }))).toBe(1.8);
      expect(isSideTrack(node("series", { track }))).toBe(true);
    }
    expect(isSideTrack(node("job", { track: "minute" }))).toBe(false);
  });
});

describe("nodeTone", () => {
  it("colours by verdict: FAIL fail, PASS bright, whatever else it says", () => {
    expect(nodeTone(node("result", { verdict: "FAIL" }))).toEqual({ token: "fail", alpha: 1 });
    expect(nodeTone(node("product", { verdict: "PASS" }))).toEqual({ token: "bright", alpha: 1 });
    expect(nodeTone(node("product", { verdict: "DESCRIPTIVE ONLY" })).token).toBe("ink");
  });

  it("draws a missing node dim at half alpha, even with an old verdict", () => {
    expect(nodeTone(node("product", { verdict: "FAIL" }, { status: "missing" }))).toEqual({ token: "dim", alpha: 0.5 });
    expect(nodeTone(node("series", {}, { status: "missing" }))).toEqual({ token: "dim", alpha: 0.5 });
  });

  it("colours by kind otherwise, and only from the palette", () => {
    expect(nodeTone(node("series")).token).toBe("dim");
    expect(nodeTone(node("result", { status: "skipped" })).token).toBe("dim");
    expect(nodeTone(node("book")).token).toBe("bright");
    expect(nodeTone(node("feed")).token).toBe("bright");
    expect(nodeTone(node("strategy")).token).toBe("ink");
  });

  it("does not trip on a verdict that is not text", () => {
    expect(nodeTone(node("product", { verdict: null })).token).toBe("ink");
    expect(nodeTone(node("product", { verdict: 3 })).token).toBe("ink");
  });
});

describe("the structure", () => {
  it("draws a circle at every ring but the book's, at the ring's radius and height", () => {
    const rings = Object.values(RADIUS).filter((r) => r > 0);
    expect(rings).toHaveLength(10);
    const milli = (r: number): number => Math.round(r * 1000);
    const seen = new Set<number>();
    for (const [p, q] of pairs(ringVerts())) {
      const { r } = polar(p);
      seen.add(milli(r));
      expect(polar(q).r).toBeCloseTo(r, 6);
      expect(p[1]).toBeCloseTo(HEIGHT * r, 6);
    }
    expect([...seen].sort((x, y) => x - y)).toEqual(rings.map(milli).sort((x, y) => x - y));
  });

  it("ticks 24 hours from the clock's ring, the sixth hours longest", () => {
    const ticks = pairs(hourTickVerts());
    expect(ticks).toHaveLength(24);
    ticks.forEach(([p, q], h) => {
      expect(polar(p).r).toBeCloseTo(RADIUS.clock, 6);
      expect(polar(q).r).toBeCloseTo(RADIUS.clock + (h % 6 ? 0.17 : 0.35), 6);
      expect(p[1]).toBeCloseTo(HEIGHT * RADIUS.clock, 6);
      expect(q[1]).toBeCloseTo(HEIGHT * RADIUS.clock, 6);
    });
  });

  it("puts one tick at each fire's hour, and none for a fire that is not a number", () => {
    const ticks = pairs(fireTickVerts([["a", 6], ["b", 18.5], ["c", NaN]]));
    expect(ticks).toHaveLength(2);
    expect(polar(ticks[0][0]).a).toBeCloseTo(hourAngle(6), 6);
    expect(polar(ticks[1][1]).a + Math.PI * 2).toBeCloseTo(hourAngle(18.5), 6);
  });

  it("gives every fire of the fixture a tick", () => {
    expect(pairs(fireTickVerts(FIXTURE.x.fires))).toHaveLength(FIXTURE.x.fires.length);
  });

  it("runs the session's arc from 09:30 to 16:00", () => {
    const arc = pairs(sessionVerts());
    expect(polar(arc[0][0]).a).toBeCloseTo(hourAngle(9.5), 6);
    expect(polar(arc[arc.length - 1][1]).a + Math.PI * 2).toBeCloseTo(hourAngle(16), 6);
  });
});

describe("nodeShot", () => {
  const p = toXYZ(RADIUS.daily, 1);

  it("frames the centre for the book, the limits, the kill switch and the strategies", () => {
    for (const k of ["limit", "kill", "strategy"] as NodeKind[]) {
      const s = nodeShot(k, 1, 2, toXYZ(1, 2), 500);
      expect(s.target).toEqual([0, 0.3, 0]);
      expect(s.radius).toBe(9.5);
      expect(s.theta).toBeCloseTo(Math.PI - 2, 10);
    }
    expect(nodeShot("book", 0, 0, [0, 0, 0], 500).theta).toBeCloseTo(Math.PI - 0.25, 10);
  });

  it("frames the whole machine at distance 19.5 for a web of more than 60 nodes", () => {
    expect(nodeShot("series", RADIUS.daily, 1, p, 61).radius).toBe(19.5);
    expect(nodeShot("series", RADIUS.daily, 1, p, 60).radius).not.toBe(19.5);
  });

  it("frames a small web's node, closer in from the outer rings' distance", () => {
    expect(nodeShot("series", RADIUS.daily, 1, p, 3).radius).toBe(15);
    expect(nodeShot("job", RADIUS.clock, 1, toXYZ(RADIUS.clock, 1), 3).radius).toBe(12.5);
  });

  it("brings the node to the near side: the camera sits on the node's side of the target", () => {
    const s = nodeShot("series", RADIUS.daily, 1, p, 3);
    // OrbitControls' spherical: azimuth 0 looks from +z, and x grows with sin(theta)
    const cam = [Math.sin(s.theta), Math.cos(s.theta)];
    expect(cam[0]).toBeCloseTo(p[0] / RADIUS.daily, 6);
    expect(cam[1]).toBeCloseTo(p[2] / RADIUS.daily, 6);
  });

  it("keeps every view inside the polar limits of the controls", () => {
    for (const s of [OVERVIEW, YOURS, nodeShot("job", 4, 1, p, 3), nodeShot("job", 4, 1, p, 100), nodeShot("book", 0, 0, [0, 0, 0], 3)]) {
      expect(s.phi).toBeGreaterThanOrEqual(0.12);
      expect(s.phi).toBeLessThanOrEqual(1.38);
    }
  });
});

describe("flights", () => {
  it("eases in and out over a cubic, from 0 to 1", () => {
    expect(ease(0)).toBe(0);
    expect(ease(1)).toBe(1);
    expect(ease(0.5)).toBe(0.5);
    expect(ease(0.25)).toBeCloseTo(4 * 0.25 ** 3, 10);
    expect(ease(0.75)).toBeCloseTo(1 - ease(0.25), 10);
  });

  it("turns the short way round", () => {
    expect(shortTheta(3, -3)).toBeCloseTo(3 + (Math.PI * 2 - 6), 10);
    expect(shortTheta(-3, 3)).toBeCloseTo(-3 - (Math.PI * 2 - 6), 10);
    expect(shortTheta(1, 1.5)).toBeCloseTo(1.5, 10);
  });

  it("moves every part of a shot together", () => {
    const m = shotAt(OVERVIEW, YOURS, 0.5);
    expect(m.radius).toBeCloseTo(25, 10);
    expect(m.phi).toBeCloseTo(0.77, 10);
    expect(m.theta).toBeCloseTo(-0.075, 10);
    expect(m.target[1]).toBeCloseTo(1.3, 10);
    expect(shotAt(OVERVIEW, YOURS, 0)).toEqual(OVERVIEW);
    expect(shotAt(OVERVIEW, YOURS, 1)).toEqual(YOURS);
  });
});

describe("zoomDistance", () => {
  it("moves out on a positive delta and in on a negative one, by OrbitControls' step", () => {
    expect(zoomDistance(20, 100, 4, 60)).toBeCloseTo(20 / 0.95, 10);
    expect(zoomDistance(20, -100, 4, 60)).toBeCloseTo(20 * 0.95, 10);
    expect(zoomDistance(20, 0, 4, 60)).toBe(20);
  });

  it("stays within the limits", () => {
    expect(zoomDistance(59, 100000, 4, 60)).toBe(60);
    expect(zoomDistance(5, -100000, 4, 60)).toBe(4);
  });
});

describe("sameView", () => {
  const view = Array.from({ length: 16 }, (_, i) => i * 0.37);

  it("holds a view the same through what damping leaves behind", () => {
    expect(sameView(view, view)).toBe(true);
    expect(sameView(view, view.map((x) => x + 1e-9))).toBe(true);
  });

  it("sees a real move in any element", () => {
    for (let i = 0; i < 16; i++) {
      const moved = [...view];
      moved[i] += 1e-3;
      expect(sameView(view, moved)).toBe(false);
    }
  });
});

describe("the screen", () => {
  const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  /** clip w is the point's z, so a point with z <= 0 is behind the camera */
  const BEHIND = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0];

  it("maps clip space to pixels, top-left at the origin", () => {
    const out = new Float32Array(2);
    expect(toScreen(IDENTITY, 0, 0, 0, 800, 600, out)).toBe(true);
    expect([...out]).toEqual([400, 300]);
    toScreen(IDENTITY, -1, 1, 0, 800, 600, out);
    expect([...out]).toEqual([0, 0]);
    toScreen(IDENTITY, 1, -1, 0, 800, 600, out);
    expect([...out]).toEqual([800, 600]);
  });

  it("refuses a point behind the camera", () => {
    const out = new Float32Array(2);
    expect(toScreen(BEHIND, 0, 0, -1, 800, 600, out)).toBe(false);
    expect(toScreen(BEHIND, 0, 0, 1, 800, 600, out)).toBe(true);
  });

  it("projects every node, NaN for one behind the camera", () => {
    const out = new Float32Array(4);
    projectNodes(BEHIND, new Float32Array([0, 0, 1, 0, 0, -1]), 800, 600, out);
    expect([...out.slice(0, 2)]).toEqual([400, 300]);
    expect(out[2]).toBeNaN();
    expect(out[3]).toBeNaN();
  });
});

describe("frameFit: how far back a narrow canvas stands the camera", () => {
  it("changes nothing on a canvas as wide as the shots, or wider", () => {
    for (const aspect of [1.2, 1.6, 2.4, 16 / 9]) expect(frameFit(aspect)).toBe(1);
  });

  it("stands back in proportion on a narrow one, so the same width of the machine fits", () => {
    expect(frameFit(0.6)).toBeCloseTo(2, 9);
    expect(frameFit(375 / 812) * (375 / 812)).toBeCloseTo(1.2, 9);
  });

  it("is 1 for a canvas with no size yet", () => {
    for (const aspect of [0, -1, Number.NaN, Infinity]) expect(frameFit(aspect)).toBe(1);
  });
});

describe("nearestNode", () => {
  it("reaches further for a fingertip, by `reach`", () => {
    const screen = new Float32Array([100, 100, 200, 200]);
    const series = Uint8Array.from([1, 0]);
    expect(nearestNode(screen, series, 110, 100)).toBeNull();
    expect(nearestNode(screen, series, 110, 100, 2)).toBe(0);
    expect(nearestNode(screen, series, 220, 200)).toBeNull();
    expect(nearestNode(screen, series, 220, 200, 2)).toBe(1);
    expect(nearestNode(screen, series, 130, 100, 2)).toBeNull();
  });

  const pick = (points: [number, number][], series: number[], x: number, y: number): number | null =>
    nearestNode(new Float32Array(points.flat()), Uint8Array.from(series), x, y);

  it("takes a series within 6 pixels and nothing further", () => {
    expect(pick([[100, 100]], [1], 105, 100)).toBe(0);
    expect(pick([[100, 100]], [1], 106, 100)).toBeNull();
  });

  it("takes anything else within 12 pixels and nothing further", () => {
    expect(pick([[100, 100]], [0], 111, 100)).toBe(0);
    expect(pick([[100, 100]], [0], 112, 100)).toBeNull();
  });

  it("takes the nearest of several", () => {
    expect(pick([[100, 100], [108, 100]], [0, 0], 106, 100)).toBe(1);
  });

  it("lets a job win a near tie with a series, as the prototype did", () => {
    expect(pick([[100, 100], [104, 100]], [1, 0], 101, 100)).toBe(1);
    expect(pick([[100, 100], [111, 100]], [1, 0], 100, 100)).toBe(0); // a series under the pointer beats a job 11 pixels off
  });

  it("never takes a node behind the camera", () => {
    expect(pick([[NaN, NaN]], [0], 100, 100)).toBeNull();
  });
});

describe("radiusOf", () => {
  it("reads the layout's radii, and a ring it does not know sits at the centre", () => {
    expect(radiusOf("clock")).toBe(RADIUS.clock);
    expect(radiusOf("nowhere" as Ring)).toBe(0);
  });
});
