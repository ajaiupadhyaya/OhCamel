/**
 * The arithmetic of the machine's scene (spec sections 6.6 and 6.7): where a fibre runs, what each mark looks like,
 * where the camera goes, and which node the pointer is on. Pure: no three.js, no DOM, no clock, so all of it runs
 * under Vitest. The scene-*.ts files put these numbers on the screen.
 *
 * Space follows layout.ts: angle 0 is the far side (-z), and the clock runs clockwise seen from above.
 */
import { HEIGHT, hourAngle, RADIUS, toXYZ } from "./layout";
import type { PaletteKey } from "./palette";
import type { MNode, NodeKind, Ring } from "./types";
import { edgeInWeb, edgeLevel } from "./web";
import type { WebSets } from "./web";

export type Vec3 = [number, number, number];

const TAU = Math.PI * 2;

/** The radius of a node's ring; a ring the page does not know sits at the centre, as layout places it. */
export function radiusOf(ring: Ring): number {
  const r = RADIUS[ring];
  return Number.isFinite(r) ? r : 0;
}

// ------------------------------------------------------------------ fibres
/** Segments in one fibre, so a fibre has `SEGMENTS + 1` points. */
export const SEGMENTS = 16;
/** How far along the angular way to the target the control point sits, which is what arches a fibre toward what it feeds. */
const CONTROL_AT = 0.72;

/**
 * The control point of the fibre from a node at radius `rA`, angle `aA` to one at `rB`, `aB`. It sits 72% of the
 * angular way there, the short way round, at the mean radius, raised above the cone to `max(hA, hB) + 0.25 + 0.12 |ΔR|`.
 * The book is at the centre and has no angle of its own, so that end takes the other's.
 */
export function fibreControl(rA: number, aA: number, rB: number, aB: number): Vec3 {
  const from = rA ? aA : aB;
  const to = rB ? aB : aA;
  const way = Math.atan2(Math.sin(to - from), Math.cos(to - from));
  const [x, , z] = toXYZ((rA + rB) / 2, from + way * CONTROL_AT);
  return [x, Math.max(HEIGHT * rA, HEIGHT * rB) + 0.25 + 0.12 * Math.abs(rA - rB), z];
}

/** Write the quadratic curve from `a` through control `c` to `b` as `SEGMENTS + 1` points of x, y, z into `out`, from point `at`. */
export function writeFibre(out: Float32Array, at: number, a: Vec3, c: Vec3, b: Vec3): void {
  for (let s = 0; s <= SEGMENTS; s++) {
    const t = s / SEGMENTS;
    const k0 = (1 - t) * (1 - t), k1 = 2 * (1 - t) * t, k2 = t * t;
    for (let d = 0; d < 3; d++) out[3 * (at + s) + d] = k0 * a[d] + k1 * c[d] + k2 * b[d];
  }
}

// ------------------------------------------------------------------ how each mark is drawn
/** A selection's brightness on nodes and fibres (spec section 6.6, "Edges"): the loom unselected, and what is outside a web. */
export const LOOM = 0.07;
export const OUTSIDE = 0.014;
/** What a fibre is doing, as its `flow` attribute: none (the loom's colour), in the up web (`reads`), the down web (`feeds`), or a running job reading it. */
export const FLOW = { none: 0, up: 1, down: 2, run: 3 } as const;

/** One lit web: the node at its centre and what `webOf` returned for it. The replay can light several at once. */
export interface CentreWeb {
  centre: number;
  web: WebSets;
}

/**
 * A fibre's `level` and `flow`. With no web lit, the loom. With webs lit, `edgeLevel(depth)` inside one (the nearest, if
 * in several) and `OUTSIDE` beyond them. A fibre outside every web that feeds a job in `running` keeps its level and
 * carries that job's pulses.
 */
export function edgeStyle(a: number, b: number, webs: readonly CentreWeb[], running: ReadonlySet<number>): { level: number; flow: number } {
  let dir: "up" | "down" | null = null;
  let depth = Infinity;
  for (const { centre, web } of webs) {
    const hit = edgeInWeb(a, b, centre, web);
    if (hit && hit.depth < depth) ({ dir, depth } = hit);
  }
  if (dir) return { level: edgeLevel(depth), flow: dir === "up" ? FLOW.up : FLOW.down };
  return { level: webs.length ? OUTSIDE : LOOM, flow: running.has(b) ? FLOW.run : FLOW.none };
}

/**
 * How lit a node is: 0.75 with no web lit; with webs lit, `centreGlow` at a centre, `1.15 - 0.09 depth` inside one (the
 * brightest, if in several; up before down, as in the prototype), and 0 beyond them.
 */
export function glowOf(i: number, webs: readonly CentreWeb[], centreGlow: number): number {
  if (!webs.length) return 0.75;
  let glow = 0;
  for (const { centre, web } of webs) {
    if (i === centre) return centreGlow;
    const depth = web.up.get(i) ?? web.down.get(i);
    if (depth !== undefined) glow = Math.max(glow, 1.15 - 0.09 * depth);
  }
  return glow;
}

const SIZE: Record<NodeKind, number> = { series: 2.4, job: 6, product: 9, result: 5.5, strategy: 5.5, feed: 11, book: 18, limit: 5.5, kill: 5.5 };
/** The tracks that are the world's main series; every other series (filings, minute, options) is drawn smaller. */
const MAIN_TRACKS: readonly unknown[] = ["daily", "macro", "factors", "13f"];

/** A series on one of the side tracks: filings, minute bars or an option chain. */
export const isSideTrack = (n: MNode): boolean => n.k === "series" && !MAIN_TRACKS.includes(n.m.track);

/** A node's size in pixels, before perspective and its glow. */
export function nodeSize(n: MNode): number {
  return isSideTrack(n) ? 1.8 : SIZE[n.k] ?? 2.4;
}

const verdictOf = (n: MNode): string => (typeof n.m.verdict === "string" ? n.m.verdict : "");
/** Whether a node's own verdict is a failure. */
export const failed = (n: MNode): boolean => /FAIL/.test(verdictOf(n));

/** A node's base colour by kind and verdict, and its alpha: PASS is `bright`, FAIL is `fail`, and a `missing` node is `dim` at half alpha. */
export function nodeTone(n: MNode): { token: PaletteKey; alpha: number } {
  if (n.status === "missing") return { token: "dim", alpha: 0.5 };
  if (failed(n)) return { token: "fail", alpha: 1 };
  if (/PASS/.test(verdictOf(n))) return { token: "bright", alpha: 1 };
  if (n.m.status === "skipped") return { token: "dim", alpha: 1 };
  switch (n.k) {
    case "series":
      return { token: "dim", alpha: 1 };
    case "product":
    case "result":
    case "strategy":
    case "limit":
      return { token: "ink", alpha: 1 };
    default:
      return { token: "bright", alpha: 1 };
  }
}

// ------------------------------------------------------------------ the structure, as line-segment vertices
const CIRCLE_SEGMENTS = 256;
const SESSION_SEGMENTS = 80;
const SESSION = [9.5, 16];
/** Everything on the clock's dial sits at the clock's height, whatever its radius. */
const dial = (r: number, a: number): Vec3 => toXYZ(r, a, HEIGHT * (RADIUS.clock - r));

/** Every ring as a circle of short segments. */
export function ringVerts(): number[] {
  const v: number[] = [];
  for (const r of Object.values(RADIUS)) {
    if (r <= 0) continue;
    for (let i = 0; i < CIRCLE_SEGMENTS; i++) v.push(...toXYZ(r, (i / CIRCLE_SEGMENTS) * TAU), ...toXYZ(r, ((i + 1) / CIRCLE_SEGMENTS) * TAU));
  }
  return v;
}

/** The 24 hour ticks, outward from the clock's ring; every sixth hour longer. */
export function hourTickVerts(): number[] {
  const v: number[] = [];
  for (let h = 0; h < 24; h++) {
    const a = hourAngle(h);
    v.push(...dial(RADIUS.clock, a), ...dial(RADIUS.clock + (h % 6 ? 0.17 : 0.35), a));
  }
  return v;
}

/** One tick just inside the clock's ring for each scheduled fire, at its New York hour. A fire that is not a number draws nothing. */
export function fireTickVerts(fires: readonly (readonly [string, number])[]): number[] {
  const v: number[] = [];
  for (const [, h] of fires) {
    if (!Number.isFinite(h)) continue;
    const a = hourAngle(h);
    v.push(...dial(RADIUS.clock - 0.18, a), ...dial(RADIUS.clock - 0.05, a));
  }
  return v;
}

/** The market session as an arc outside the clock's ring, 09:30 to 16:00. */
export function sessionVerts(): number[] {
  const v: number[] = [];
  const [open, close] = SESSION;
  const at = (i: number): Vec3 => dial(RADIUS.clock + 0.37, hourAngle(open + (i / SESSION_SEGMENTS) * (close - open)));
  for (let i = 0; i < SESSION_SEGMENTS; i++) v.push(...at(i), ...at(i + 1));
  return v;
}

// ------------------------------------------------------------------ the camera
/** A camera position: what it looks at, and from where (OrbitControls' spherical: distance, polar angle, azimuth). */
export interface Shot {
  target: Vec3;
  radius: number;
  phi: number;
  theta: number;
}

/** The two fixed views of the whole machine (tour.ts, `TourCam`). */
export const OVERVIEW: Shot = { target: [0, 1.4, 0], radius: 27, phi: 0.92, theta: 0.35 };
export const YOURS: Shot = { target: [0, 1.2, 0], radius: 23, phi: 0.62, theta: -0.5 };
/** The replay's view: the clock from above, close enough to read the jobs on it. */
export const CLOCK: Shot = { target: [0, 1.2, 0], radius: 19, phi: 0.5, theta: 0.2 };

/** Above this many nodes a web is framed whole, not by its node. */
const BIG_WEB = 60;
const CENTRAL: readonly NodeKind[] = ["book", "limit", "kill", "strategy"];

/** The azimuth that puts a ring angle on the near side. */
const nearSide = (a: number): number => Math.PI - a;

/**
 * Where the camera goes to frame a node of kind `k` at radius `r`, angle `a` and position `p`, whose web holds `webSize`
 * nodes. The book, limits, kill switch and strategies frame the centre. Otherwise a web of more than 60 nodes frames the
 * whole machine at distance 19.5, and a smaller one frames its node, with the node's angle brought to the near side.
 */
export function nodeShot(k: NodeKind, r: number, a: number, p: Vec3, webSize: number): Shot {
  if (CENTRAL.includes(k)) return { target: [0, 0.3, 0], radius: 9.5, phi: 0.95, theta: nearSide(k === "book" ? 0.25 : a) };
  const big = webSize > BIG_WEB;
  const pull = big ? 0.3 : 0.55;
  return {
    target: [p[0] * pull, big ? 1.2 : HEIGHT * r * 0.6 + 0.4, p[2] * pull],
    radius: big ? 19.5 : r > 6 ? 15 : 12.5,
    phi: big ? 0.82 : 0.98,
    theta: nearSide(a),
  };
}

/** Ease-in-out cubic, 0 to 1. */
export const ease = (t: number): number => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** An azimuth equal to `to` but reached from `from` the short way round. */
export function shortTheta(from: number, to: number): number {
  const d = to - from;
  return from + Math.atan2(Math.sin(d), Math.cos(d));
}

/** Where a flight from `a` to `b` is at `e` of the way. */
export function shotAt(a: Shot, b: Shot, e: number): Shot {
  const mix = (x: number, y: number): number => x + (y - x) * e;
  return {
    target: [mix(a.target[0], b.target[0]), mix(a.target[1], b.target[1]), mix(a.target[2], b.target[2])],
    radius: mix(a.radius, b.radius),
    phi: mix(a.phi, b.phi),
    theta: mix(a.theta, b.theta),
  };
}

/** The camera distance after a wheel of `deltaY` (the same step as OrbitControls' wheel: down moves out), held within `min` and `max`. */
export function zoomDistance(distance: number, deltaY: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, distance * Math.pow(0.95, -deltaY * 0.01)));
}

// ------------------------------------------------------------------ the screen
/** Matrix elements closer than this are the same view: on a 1,440-pixel canvas it is about a thousandth of a pixel. */
const SAME_VIEW = 1e-6;

/**
 * Whether two view-projection matrices are the same view. Not an exact compare: damping decays toward rest without
 * reaching it, so an exact one would call a still camera moving for seconds after a drag, and redo the labels each frame.
 */
export function sameView(a: ArrayLike<number>, b: ArrayLike<number>): boolean {
  for (let i = 0; i < 16; i++) if (Math.abs(a[i] - b[i]) > SAME_VIEW) return false;
  return true;
}

/**
 * Project world point (x, y, z) through column-major view-projection matrix `m` to pixels on a `w` by `h` canvas, into
 * `out[at]` and `out[at + 1]`. False, with `out` untouched, when the point is behind the camera.
 */
export function toScreen(m: ArrayLike<number>, x: number, y: number, z: number, w: number, h: number, out: Float32Array, at = 0): boolean {
  const clipW = m[3] * x + m[7] * y + m[11] * z + m[15];
  if (clipW <= 0) return false;
  out[at] = (((m[0] * x + m[4] * y + m[8] * z + m[12]) / clipW) * 0.5 + 0.5) * w;
  out[at + 1] = (-((m[1] * x + m[5] * y + m[9] * z + m[13]) / clipW) * 0.5 + 0.5) * h;
  return true;
}

/** Every node of `pos` (x, y, z each) in pixels into `out` (x, y each); NaN for a node behind the camera. */
export function projectNodes(m: ArrayLike<number>, pos: Float32Array, w: number, h: number, out: Float32Array): void {
  const n = pos.length / 3;
  for (let i = 0; i < n; i++) {
    if (!toScreen(m, pos[3 * i], pos[3 * i + 1], pos[3 * i + 2], w, h, out, 2 * i)) out[2 * i] = out[2 * i + 1] = NaN;
  }
}

/** How close the pointer must be to a series, and to anything else, in pixels. */
const PICK_SERIES = 6;
const PICK_OTHER = 12;
/** A node other than a series wins a near tie with one: hundreds of series sit round every job and product. */
const PICK_BIAS = 4;

/**
 * The node nearest (x, y): within 6 pixels of a series or 12 of anything else, or null. `screen` is what `projectNodes`
 * wrote; `series[i]` is 1 for a series. Scored as the prototype scored it, the distance less 4 pixels for what is not a series.
 */
export function nearestNode(screen: Float32Array, series: Uint8Array, x: number, y: number): number | null {
  let best: number | null = null;
  let bestScore = Infinity;
  for (let i = 0; i < series.length; i++) {
    const dx = screen[2 * i] - x, dy = screen[2 * i + 1] - y;
    if (!(Math.abs(dx) < PICK_OTHER && Math.abs(dy) < PICK_OTHER)) continue; // also skips a node behind the camera (NaN)
    const d = Math.sqrt(dx * dx + dy * dy);
    const isSeries = series[i] === 1;
    if (d >= (isSeries ? PICK_SERIES : PICK_OTHER)) continue;
    const score = isSeries ? d : d - PICK_BIAS;
    if (score < bestScore) {
      bestScore = score;
      best = i;
    }
  }
  return best;
}
