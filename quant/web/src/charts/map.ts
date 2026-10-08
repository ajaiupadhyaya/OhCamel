/**
 * Direct labels for a scatter: each label tries right, left, above, below of its mark (then
 * further out), taking the first spot that stays inside the box and clears every label
 * already placed. Greedy, in input order, so put the important points first.
 */
export interface LabelBox {
  /** Mark centre. */
  x: number;
  y: number;
  /** Label size. */
  w: number;
  h: number;
}

export interface Placed {
  x: number;
  y: number;
  w: number;
  h: number;
}

const GAP = 6;

export function placeLabels(items: LabelBox[], box: { w: number; h: number }): Placed[] {
  const done: Placed[] = [];
  const hits = (p: Placed) => done.some((q) => p.x < q.x + q.w && q.x < p.x + p.w && p.y < q.y + q.h && q.y < p.y + p.h);
  const inside = (p: Placed) => p.x >= 0 && p.y >= 0 && p.x + p.w <= box.w && p.y + p.h <= box.h;
  for (const it of items) {
    const cands: Placed[] = [];
    for (let ring = 0; ring < 6; ring++) {
      const d = GAP + ring * (it.h + 1);
      cands.push(
        { x: it.x + d, y: it.y - it.h / 2, w: it.w, h: it.h },
        { x: it.x - d - it.w, y: it.y - it.h / 2, w: it.w, h: it.h },
        { x: it.x - it.w / 2, y: it.y - d - it.h, w: it.w, h: it.h },
        { x: it.x - it.w / 2, y: it.y + d, w: it.w, h: it.h },
        { x: it.x + GAP, y: it.y - it.h / 2 - d, w: it.w, h: it.h },
        { x: it.x + GAP, y: it.y - it.h / 2 + d, w: it.w, h: it.h },
      );
    }
    const clamp = (p: Placed): Placed => ({ ...p, x: Math.min(Math.max(0, p.x), box.w - p.w), y: Math.min(Math.max(0, p.y), box.h - p.h) });
    const pick = cands.find((p) => inside(p) && !hits(p)) ?? cands.map(clamp).find((p) => !hits(p)) ?? clamp(cands[0]);
    done.push(pick);
  }
  return done;
}
