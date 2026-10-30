/**
 * Which labels the machine shows (spec section 6.6, "Labels"). Pure: no three.js, no DOM, no clock.
 *
 * The labels are a DOM overlay above the canvas. Each frame the scene projects every candidate to a screen rectangle
 * and hands them here; this decides which to show, by priority, never two overlapping. It runs every animation
 * frame, so it keeps its work in flat typed arrays and allocates nothing per candidate beyond the sort.
 */

/** Priority by kind of label: a lower number is placed first and so wins any overlap. */
export const LABEL_PRI: Record<string, number> = {
  selected: 0,
  "in-web": 1,
  book: 2,
  feed: 3,
  product: 4,
  kill: 5,
  job: 6,
  section: 7,
  hour: 8,
};

/** One label the scene would like to show: a screen rectangle (top-left corner and size, in pixels) and a priority. */
export interface LabelCand {
  /** Names the label to the caller; unique among the candidates of one call. */
  key: string;
  x: number;
  y: number;
  w: number;
  h: number;
  pri: number;
}

/** Series labels (hundreds of them) are shown only when the selected web is small enough to read. */
export function seriesLabelsAllowed(webSize: number): boolean {
  return webSize < 140;
}

/**
 * The keys of the labels to show. Greedy: candidates are taken by `pri`, ties in input order, and one is dropped if
 * its rectangle, grown by `pad` pixels on every side, overlaps one already placed (so two labels end up at least
 * `pad` apart; exactly `pad` apart is fine). A label that loses to a placed one casts no shadow of its own, so it
 * hides nothing after it. A candidate whose rectangle is not a finite number cannot be placed and is dropped.
 * The input is read, never changed.
 */
export function placeLabels(c: LabelCand[], pad = 4): Set<string> {
  const order: number[] = [];
  for (let i = 0; i < c.length; i++) {
    const a = c[i];
    if (Number.isFinite(a.x + a.y + a.w + a.h)) order.push(i);
  }
  order.sort((i, j) => c[i].pri - c[j].pri || i - j);

  const placed = new Set<string>();
  // The placed rectangles as [x0, y0, x1, y1] runs of four.
  const box = new Float64Array(order.length * 4);
  let n = 0;
  for (const i of order) {
    const a = c[i];
    const x0 = a.x - pad;
    const y0 = a.y - pad;
    const x1 = a.x + a.w + pad;
    const y1 = a.y + a.h + pad;
    let hit = false;
    for (let k = 0; k < n * 4; k += 4) {
      if (x0 < box[k + 2] && x1 > box[k] && y0 < box[k + 3] && y1 > box[k + 1]) {
        hit = true;
        break;
      }
    }
    if (hit) continue;
    box[n * 4] = a.x;
    box[n * 4 + 1] = a.y;
    box[n * 4 + 2] = a.x + a.w;
    box[n * 4 + 3] = a.y + a.h;
    n++;
    placed.add(a.key);
  }
  return placed;
}
