import { describe, expect, it } from "vitest";
import { LABEL_PRI, placeLabels, seriesLabelsAllowed } from "./labels";
import type { LabelCand } from "./labels";

const cand = (key: string, x: number, y: number, pri = 0, w = 100, h = 20): LabelCand => ({ key, x, y, w, h, pri });
const keys = (s: Set<string>): string[] => [...s].sort();

describe("LABEL_PRI", () => {
  it("ranks the selected thing first and the hours last", () => {
    expect(LABEL_PRI).toEqual({ selected: 0, "in-web": 1, book: 2, feed: 3, product: 4, kill: 5, job: 6, section: 7, hour: 8 });
  });
});

describe("seriesLabelsAllowed", () => {
  it("allows series labels only inside a web of fewer than 140 nodes", () => {
    expect(seriesLabelsAllowed(0)).toBe(true);
    expect(seriesLabelsAllowed(139)).toBe(true);
    expect(seriesLabelsAllowed(140)).toBe(false);
    expect(seriesLabelsAllowed(5000)).toBe(false);
  });
});

describe("placeLabels", () => {
  it("places every label when none touches another", () => {
    const c = [cand("a", 0, 0), cand("b", 200, 0), cand("c", 0, 100), cand("d", 200, 100)];
    expect(keys(placeLabels(c))).toEqual(["a", "b", "c", "d"]);
  });

  it("places nothing from nothing", () => {
    expect(placeLabels([]).size).toBe(0);
  });

  it("keeps the lower pri of two overlapping labels, whichever comes first", () => {
    const winnerSecond = [cand("low", 0, 0, 5), cand("high", 50, 5, 1)];
    expect(keys(placeLabels(winnerSecond))).toEqual(["high"]);
    const winnerFirst = [cand("high", 50, 5, 1), cand("low", 0, 0, 5)];
    expect(keys(placeLabels(winnerFirst))).toEqual(["high"]);
  });

  it("breaks a tie in pri by input order", () => {
    const c = [cand("first", 0, 0, 3), cand("second", 10, 0, 3), cand("third", 20, 0, 3)];
    expect(keys(placeLabels(c))).toEqual(["first"]);
    expect(keys(placeLabels([...c].reverse()))).toEqual(["third"]);
  });

  it("does not let a dropped label shield a lower-priority one it overlapped", () => {
    // a (pri 0) covers b (pri 1); b would have covered c (pri 2). b is dropped, so c, clear of a, stands.
    const c = [cand("a", 0, 0, 0), cand("b", 90, 0, 1), cand("c", 190, 0, 2)];
    expect(keys(placeLabels(c))).toEqual(["a", "c"]);
  });

  it("collides two labels that touch, because each is padded", () => {
    const c = [cand("a", 0, 0, 0), cand("b", 100, 0, 1)];
    expect(keys(placeLabels(c, 4))).toEqual(["a"]);
  });

  it("collides two labels that are apart by less than the padding, side by side and stacked", () => {
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 103, 0, 1)], 4))).toEqual(["a"]);
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 0, 23, 1)], 4))).toEqual(["a"]);
  });

  it("places two labels that are apart by at least the padding", () => {
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 104, 0, 1)], 4))).toEqual(["a", "b"]);
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 0, 24, 1)], 4))).toEqual(["a", "b"]);
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 105, 0, 1)]))).toEqual(["a", "b"]);
  });

  it("pads the corners too: a label diagonal to another within the padding collides", () => {
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 103, 23, 1)], 4))).toEqual(["a"]);
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 104, 24, 1)], 4))).toEqual(["a", "b"]);
  });

  it("honours a pad of zero: abutting labels both stand, overlapping ones do not", () => {
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 100, 0, 1)], 0))).toEqual(["a", "b"]);
    expect(keys(placeLabels([cand("a", 0, 0, 0), cand("b", 99, 0, 1)], 0))).toEqual(["a"]);
  });

  it("collides a small label wholly inside a large one", () => {
    const c = [cand("small", 40, 40, 1, 10, 10), cand("large", 0, 0, 0, 200, 200)];
    expect(keys(placeLabels(c))).toEqual(["large"]);
  });

  it("works in negative and far-off-screen coordinates", () => {
    const c = [cand("a", -500, -300, 0), cand("b", -450, -300, 1), cand("c", -2000, 90000, 1), cand("d", -1990, 90010, 2)];
    expect(keys(placeLabels(c))).toEqual(["a", "c"]);
  });

  it("collides a label spanning a great width with one far along it", () => {
    const c = [cand("wide", 0, 0, 0, 5000, 20), cand("far", 4900, 5, 1)];
    expect(keys(placeLabels(c))).toEqual(["wide"]);
  });

  it("drops a label whose rectangle is not a finite number, and keeps the rest", () => {
    const c = [cand("nan", Number.NaN, 0, 0), cand("inf", 0, Number.POSITIVE_INFINITY, 0), cand("wide", 0, 0, 1, Number.NaN), cand("ok", 0, 0, 2)];
    expect(keys(placeLabels(c))).toEqual(["ok"]);
  });

  it("does not change the candidates or their order", () => {
    const c = [cand("c", 0, 0, 5), cand("a", 50, 0, 1), cand("b", 400, 0, 3)];
    const before = JSON.stringify(c);
    const frozen = Object.freeze(c.map((x) => Object.freeze({ ...x }))) as LabelCand[];
    expect(() => placeLabels(frozen)).not.toThrow();
    placeLabels(c);
    expect(JSON.stringify(c)).toBe(before);
  });

  it("gives the same answer when asked twice of the same candidates", () => {
    const c = [cand("c", 0, 0, 5), cand("a", 50, 0, 1), cand("b", 400, 0, 3), cand("d", 60, 10, 1)];
    expect([...placeLabels(c)]).toEqual([...placeLabels(c)]);
  });

  it("answers as a brute-force pass over the same candidates would", () => {
    // A deterministic scatter with mixed sizes and many ties in pri: the answer is checked against the plain
    // definition (sort by pri then input order; keep a label unless its padded rectangle meets a kept one).
    let s = 12345;
    const rnd = (): number => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
    const c: LabelCand[] = Array.from({ length: 600 }, (_, i) =>
      cand(`k${i}`, Math.floor(rnd() * 1600) - 100, Math.floor(rnd() * 900) - 100, Math.floor(rnd() * 5), 20 + Math.floor(rnd() * 140), 10 + Math.floor(rnd() * 30)),
    );
    for (const pad of [0, 4, 11]) {
      const order = c.map((_, i) => i).sort((i, j) => c[i].pri - c[j].pri || i - j);
      const kept: LabelCand[] = [];
      for (const i of order) {
        const a = c[i];
        const hit = kept.some((b) => a.x - pad < b.x + b.w && a.x + a.w + pad > b.x && a.y - pad < b.y + b.h && a.y + a.h + pad > b.y);
        if (!hit) kept.push(a);
      }
      const got = placeLabels(c, pad);
      expect(got.size).toBe(kept.length);
      expect(keys(got)).toEqual(kept.map((k) => k.key).sort());
    }
  });

  describe("speed", () => {
    /** The best of a few runs, so a loaded box cannot fail a fast function on one bad scheduling slice. */
    const best = (f: () => void, runs = 5): number => {
      let t = Number.POSITIVE_INFINITY;
      for (let r = 0; r < runs; r++) {
        const t0 = performance.now();
        f();
        t = Math.min(t, performance.now() - t0);
      }
      return t;
    };

    it("places 2,000 labels spread across a screen in under 20 ms", () => {
      // 50 columns by 40 rows, every label clear of its neighbours: nothing is dropped, the worst case for a scan.
      const c: LabelCand[] = Array.from({ length: 2000 }, (_, i) => cand(`s${i}`, (i % 50) * 80, Math.floor(i / 50) * 30, i % 9, 60, 14));
      let placed = 0;
      const ms = best(() => {
        placed = placeLabels(c).size;
      });
      expect(placed).toBe(2000);
      expect(ms).toBeLessThan(20);
    });

    it("places 2,000 labels piled onto a small area in under 20 ms", () => {
      let s = 99;
      const rnd = (): number => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
      const c: LabelCand[] = Array.from({ length: 2000 }, (_, i) => cand(`p${i}`, rnd() * 1200, rnd() * 700, i % 9, 40 + rnd() * 80, 14));
      let placed = 0;
      const ms = best(() => {
        placed = placeLabels(c).size;
      });
      expect(placed).toBeGreaterThan(0);
      expect(placed).toBeLessThan(2000);
      expect(ms).toBeLessThan(20);
    });
  });
});
