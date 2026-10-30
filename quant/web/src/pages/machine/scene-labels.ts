/**
 * Which labels the scene offers, and where they fall on the screen (spec section 6.6, "Labels"). Pure: labels are data,
 * not WebGL; React renders the survivors as text (Task 17).
 *
 * `labelBook` lists a web's candidates when the web changes. `placeFrame` projects those and asks `placeLabels` which
 * to show, so a frame's work is proportional to the labels on offer, never to the nodes or edges.
 *
 * A label's `cls` is its kind, then its state, as space-separated names:
 * - `big`: a feed or the book. `hour`: an hour, or "market session". `sect`: a section of the daily ring. `now`: the hand.
 * - `sel`: the selection. `up`: in what it reads. `down`: in what it feeds. `fail`: failed a gate, instead of `up` or `down`.
 * - `near`: a job or the kill switch with nothing selected, shown fainter. `off`: outside the selected web, darkened.
 */
import { hm } from "./fmt";
import { LABEL_PRI, placeLabels, seriesLabelsAllowed } from "./labels";
import type { LabelCand } from "./labels";
import { circularMean, hourAngle, RADIUS, toXYZ } from "./layout";
import { failed, isSideTrack, toScreen } from "./scene-geom";
import type { CentreWeb, Vec3 } from "./scene-geom";
import type { MNode } from "./types";
import type { GraphIndex } from "./web";

/** One label on offer: its text, its anchor in the world, and what placing it needs. */
export interface LabelSpec {
  key: string;
  text: string;
  at: Vec3;
  w: number;
  h: number;
  pri: number;
  cls: string;
}

/** One label to show. `x, y` is its anchor in canvas pixels: the middle of its bottom edge, as the prototype's `translate(-50%, -100%)` sets it. */
export interface PlacedLabel {
  key: string;
  text: string;
  x: number;
  y: number;
  cls: string;
}

/** A label's size is estimated from its text: the page's label face is a monospace, 10.5 px (11.5 for `big`), so the width is the characters'. */
const CHAR = 6.5;
const CHAR_BIG = 7.1;
const LINE = 13;

/** How far above its node a label sits, in world units. */
const LIFT: Record<MNode["k"], number> = { feed: 0.32, product: 0.22, job: 0.16, book: 0.34, kill: 0.18, limit: 0.14, strategy: 0.14, result: 0.14, series: 0.12 };

const size = (text: string, big = false): { w: number; h: number } => ({ w: text.length * (big ? CHAR_BIG : CHAR), h: LINE });

const withNote = (label: string, note: unknown): string => (typeof note === "string" && note ? `${label} · ${note}` : label);

/** What a node's label says: the short label, and for a feed who it comes from, for a product its name, for the kill switch its state. */
function textOf(n: MNode): string {
  if (n.k === "feed") return withNote(n.l, n.m.who);
  if (n.k === "product") return withNote(n.l, n.n);
  if (n.k === "kill") return withNote(n.l, n.m.state);
  return n.l;
}

/** The kinds labelled with nothing selected, by their priority; any other kind has a label only in a web. */
const STANDING: Partial<Record<MNode["k"], number>> = {
  book: LABEL_PRI.book, feed: LABEL_PRI.feed, product: LABEL_PRI.product, kill: LABEL_PRI.kill, job: LABEL_PRI.job,
};
/** Labelled, with nothing selected, as fainter than the rest. */
const FAINT: readonly MNode["k"][] = ["job", "kill"];

/** The daily ring's sections. */
const SECTIONS: Record<string, string> = { etf: "ETFS", eq: "S&P 500 + NASDAQ-100", fred: "FRED", ff: "FACTORS", "13f": "13F" };

const joined = (...names: (string | false)[]): string => names.filter(Boolean).join(" ");

export interface LabelBook {
  /** The labels on offer with `webs` lit, the hand at `nowHour`. */
  active(webs: readonly CentreWeb[], nowHour: number): LabelSpec[];
}

/** The labels of a graph, laid out once; `pos` holds x, y, z for each node. */
export function labelBook(gi: GraphIndex, pos: Float32Array, angle: Float32Array): LabelBook {
  const { nodes } = gi.out;

  // what never changes: the node labels and the dial
  const texts = nodes.map(textOf);
  const anchors = nodes.map((n, i): Vec3 => [pos[3 * i], pos[3 * i + 1] + (LIFT[n.k] ?? 0.14), pos[3 * i + 2]]);

  const dial: LabelSpec[] = [0, 3, 6, 9, 12, 15, 18, 21].map((h) => {
    const text = hm(h).replace(":00", "");
    return { key: `@hour:${h}`, text, at: toXYZ(RADIUS.clock + 0.7, hourAngle(h)), ...size(text), pri: LABEL_PRI.hour, cls: "hour" };
  });
  const session = "market session";
  dial.push({ key: "@session", text: session, at: toXYZ(RADIUS.clock + 0.8, hourAngle(12.75)), ...size(session), pri: LABEL_PRI.hour, cls: "hour" });

  const sections: LabelSpec[] = Object.entries(SECTIONS).flatMap(([sec, text]) => {
    const angles = nodes.flatMap((n, i) => (n.at.sec === sec ? [angle[i]] : []));
    if (!angles.length) return [];
    return [{ key: `@sect:${sec}`, text, at: toXYZ(RADIUS.daily + 0.55, circularMean(angles), 0.05), ...size(text), pri: LABEL_PRI.section, cls: "sect" }];
  });

  return {
    active(webs, nowHour) {
      const lit = webs.length > 0;
      const out: LabelSpec[] = [];
      const webSize = webs.reduce((sum, { web }) => sum + web.up.size + web.down.size, 0);
      const series = seriesLabelsAllowed(webSize);

      nodes.forEach((n, i) => {
        const at = anchors[i], text = texts[i], key = n.id;
        const big = n.k === "feed" || n.k === "book";
        const base = joined(big && "big", failed(n) && "fail");
        const push = (pri: number, state: string | false): void => {
          out.push({ key, text, at, ...size(text, big), pri, cls: joined(base, state) });
        };
        if (webs.some((w) => w.centre === i)) {
          push(LABEL_PRI.selected, "sel");
          return;
        }
        const up = webs.some((w) => w.web.up.has(i));
        if (up || webs.some((w) => w.web.down.has(i))) {
          if (n.k === "series" && (!series || isSideTrack(n))) return;
          push(LABEL_PRI["in-web"], !failed(n) && (up ? "up" : "down"));
          return;
        }
        const pri = STANDING[n.k];
        if (pri === undefined) return;
        push(pri, lit ? "off" : FAINT.includes(n.k) && "near");
      });

      const state = lit ? " off" : "";
      out.push({ key: "@now", text: "now", at: toXYZ(RADIUS.clock + 0.8, hourAngle(nowHour), 0.1), ...size("now"), pri: LABEL_PRI.section, cls: `now${state}` });
      for (const s of sections) out.push({ ...s, cls: s.cls + state });
      for (const s of dial) out.push({ ...s, cls: s.cls + state });
      return out;
    },
  };
}

const screen = new Float32Array(2);

/**
 * The labels to show this frame: `specs` projected through view-projection matrix `m` on a `w` by `h` canvas, those
 * behind the camera or wholly off the canvas left out, the rest through `placeLabels`, which takes a rectangle's
 * top-left corner while a label hangs from its anchor at the middle of its bottom edge.
 */
export function placeFrame(specs: readonly LabelSpec[], m: ArrayLike<number>, w: number, h: number): PlacedLabel[] {
  const cands: LabelCand[] = [];
  const shown = new Map<string, PlacedLabel>();
  for (const s of specs) {
    if (!toScreen(m, s.at[0], s.at[1], s.at[2], w, h, screen)) continue;
    const x = screen[0], y = screen[1];
    if (x + s.w / 2 < 0 || x - s.w / 2 > w || y < 0 || y - s.h > h) continue;
    cands.push({ key: s.key, x: x - s.w / 2, y: y - s.h, w: s.w, h: s.h, pri: s.pri });
    shown.set(s.key, { key: s.key, text: s.text, x, y, cls: s.cls });
  }
  const placed: PlacedLabel[] = [];
  for (const key of placeLabels(cands)) placed.push(shown.get(key)!);
  return placed;
}
