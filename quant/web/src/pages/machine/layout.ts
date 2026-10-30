/**
 * Where each node of `GET /api/machine` sits in space (spec section 6.3). Pure: no three.js, no DOM, no clock.
 *
 * The server sends semantics, not coordinates: a node's `ring` and its `at` (a section and index, a New York hour,
 * or the node it sits beside). This turns them into an angle and a position. The rings sit in the x-z plane and
 * narrow, like a shallow cone, to the book at the centre: feeds outside, then the warehouse tracks, the 24-hour
 * clock, products, results, strategies, limits, and the book. The clock runs clockwise seen from above, midnight at
 * the far side (-z). Which hour a job sits at is the server's decision (`at.h`: the single fire, the midpoint of a
 * run of fires under 12 hours apart, otherwise the last fire); the angles here follow from it.
 *
 * Every number it returns is finite, whatever the wire holds: a node the wire does not place (a product the clock
 * has no entry for arrives with `at: {}`) or places by a node that is absent sits at angle 0.
 */
import type { EdgeKind, MachineOut, MNode, Ring } from "./types";

const TAU = Math.PI * 2;

/** The radius of each ring. */
export const RADIUS: Record<Ring, number> = {
  feeds: 8.4,
  daily: 7,
  filings: 6.6,
  minute: 6.25,
  options: 5.9,
  clock: 4.25,
  products: 3.25,
  results: 2.35,
  strategies: 1.45,
  limits: 0.78,
  book: 0,
};
/** A ring rises `HEIGHT` for each unit of radius, so the rings fall away to the book at the centre. */
export const HEIGHT = 0.34;

/** How far a product's results spread round its angle, by product id (radians). Any other product's is `DEFAULT_RESULT_WIDTH`. */
export const RESULT_WIDTH: Record<string, number> = {
  "p:farm.sweep": 0.95,
  "p:vol.forecast_league": 0.22,
  "p:cov.league": 0.36,
  "p:risk.mc_atlas": 0.22,
};
const DEFAULT_RESULT_WIDTH = 0.3;

/** The daily ring's sections, laid end to end in this order, each followed by a gap of `SECTION_GAP` empty slots. */
const SECTIONS = ["etf", "eq", "fred", "ff", "13f"] as const;
const SECTION_GAP = 6;
const DAILY_START_HOUR = 0.6;
const STRATEGY_START_HOUR = 19.5;
const LIMIT_START_HOUR = 1;
/** The research service sits this far from the daily signals it sits beside, and the filings feed this far off the series it reads. */
const RESEARCH_OFFSET = 0.12;
const FILINGS_FEED_OFFSET = 0.18;

/** The angle of a New York hour: `hour / 24` of a turn from midnight. */
export const hourAngle = (h: number): number => (h / 24) * TAU;

/** A point on a ring of radius `r` at angle `a`, `dy` above it. Angle 0 is the far side (-z); `+ 0` turns a negative zero into 0. */
export function toXYZ(r: number, a: number, dy = 0): [number, number, number] {
  return [r * Math.sin(a) + 0, HEIGHT * r + dy + 0, -r * Math.cos(a) + 0];
}

const spreadAt = (center: number, n: number, width: number, i: number): number =>
  n > 1 ? center + (i - (n - 1) / 2) * (width / (n - 1)) : center;

/** `n` angles evenly across `width`, centred on `center`: `[center]` for one, none for none. */
export function spread(center: number, n: number, width: number): number[] {
  return Array.from({ length: Math.max(0, n) }, (_, i) => spreadAt(center, n, width, i));
}

/** The mean direction of `angles`, in (-pi, pi]: 6.2 and 0.1 meet near 0, not near pi. 0 for none. */
export function circularMean(angles: readonly number[]): number {
  let s = 0;
  let c = 0;
  for (const a of angles) {
    s += Math.sin(a);
    c += Math.cos(a);
  }
  return Math.atan2(s, c);
}

const wrap = (a: number): number => ((a % TAU) + TAU) % TAU;

/**
 * Where each daily-ring section starts, in slots, and how many slots the ring has in all. A section's size is its
 * highest index plus one, counted from the series the payload holds, so an index the payload skips keeps its slot
 * and the sections after it stay where they are.
 */
function sectionSlots(nodes: readonly MNode[]): { start: Map<string, number>; total: number } {
  const size = new Map<string, number>();
  for (const { at } of nodes) if (at.sec !== undefined) size.set(at.sec, Math.max(size.get(at.sec) ?? 0, (at.i ?? 0) + 1));
  const start = new Map<string, number>();
  let slots = 0;
  for (const sec of SECTIONS) {
    start.set(sec, slots);
    slots += (size.get(sec) ?? 0) + SECTION_GAP;
  }
  return { start, total: slots };
}

/** Edge heads by tail, for the edges of one kind. */
function successors(out: MachineOut, kind: EdgeKind): Map<number, number[]> {
  const into = new Map<number, number[]>();
  for (const [from, to, k] of out.edges) {
    if (k !== kind) continue;
    const list = into.get(from);
    if (list) list.push(to);
    else into.set(from, [to]);
  }
  return into;
}

/**
 * Every node's angle (radians, 0 to 2pi) and position, in the order of `out.nodes`: `pos` holds x, y, z for each,
 * `angle` one number. `byId` maps a node id to its index in `out.nodes`.
 *
 * - Daily series sit in their section's slot; filings, minute and option series at their ticker's angle.
 * - A job sits at its hour; a product at its job's angle, and the research service just beside the daily signals.
 * - Results spread round their product's angle; strategies, then limits and the kill switch, are evenly spaced.
 * - The book is at the origin.
 * - A feed sits at the circular mean of the series its jobs write (a feed whose jobs write nothing, at those jobs).
 */
export function layout(out: MachineOut, byId: ReadonlyMap<string, number>): { pos: Float32Array; angle: Float32Array } {
  const { nodes } = out;
  const { start, total } = sectionSlots(nodes);
  const pulls = successors(out, "pull");
  const writes = successors(out, "write");

  const done = new Float64Array(nodes.length);
  // 0: not placed yet, 1: being placed, 2: placed. A node found being placed is one that, through `of`/`beside`,
  // is placed by itself; it reads as angle 0 rather than looping.
  const state = new Uint8Array(nodes.length);

  /** The angle of the node `id`, or 0 when it is not in the graph or not given. */
  const angleOfId = (id: string | undefined): number => {
    const i = id === undefined ? undefined : byId.get(id);
    return i === undefined ? 0 : angleOf(i);
  };

  const feedAngle = (i: number): number => {
    const jobs = pulls.get(i) ?? [];
    const series = new Set(jobs.flatMap((j) => writes.get(j) ?? []));
    const a = circularMean([...(series.size ? series : jobs)].map(angleOf));
    return nodes[i].id === "feed:sec" ? a + FILINGS_FEED_OFFSET : a;
  };

  const place = (i: number): number => {
    const { k, at } = nodes[i];
    const n = Math.max(1, at.n ?? 1);
    switch (k) {
      case "book":
        return 0;
      case "feed":
        return feedAngle(i);
      case "series":
        if (at.sec === undefined) return angleOfId(at.of);
        return hourAngle(DAILY_START_HOUR) + (((start.get(at.sec) ?? 0) + (at.i ?? 0) + 0.5) / total) * TAU;
      case "job":
        return hourAngle(at.h ?? 0);
      case "product":
        return at.beside === undefined ? angleOfId(at.of) : angleOfId(at.beside) + RESEARCH_OFFSET;
      case "result":
        return spreadAt(angleOfId(at.of), n, RESULT_WIDTH[at.of ?? ""] ?? DEFAULT_RESULT_WIDTH, at.i ?? 0);
      case "strategy":
        return ((at.i ?? 0) / n) * TAU + hourAngle(STRATEGY_START_HOUR);
      case "limit":
      case "kill":
        return ((at.i ?? 0) / n) * TAU + hourAngle(LIMIT_START_HOUR);
    }
  };

  function angleOf(i: number): number {
    if (state[i] === 2) return done[i];
    if (state[i] === 1) return 0;
    state[i] = 1;
    const a = place(i);
    done[i] = Number.isFinite(a) ? wrap(a) : 0;
    state[i] = 2;
    return done[i];
  }

  const pos = new Float32Array(3 * nodes.length);
  const angle = new Float32Array(nodes.length);
  for (let i = 0; i < nodes.length; i++) {
    const a = angleOf(i);
    const r = RADIUS[nodes[i].ring];
    const [x, y, z] = toXYZ(Number.isFinite(r) ? r : 0, a);
    angle[i] = a;
    pos[3 * i] = x;
    pos[3 * i + 1] = y;
    pos[3 * i + 2] = z;
  }
  return { pos, angle };
}
