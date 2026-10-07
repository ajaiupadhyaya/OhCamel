import { describe, expect, it } from "vitest";
import { placeLabels } from "./map";

const overlap = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

describe("placeLabels", () => {
  it("puts a lone label to the right of its mark", () => {
    const [p] = placeLabels([{ x: 50, y: 50, w: 20, h: 10 }], { w: 200, h: 200 });
    expect(p.x).toBeGreaterThan(50);
    expect(p.y + p.h / 2).toBeCloseTo(50);
  });
  it("labels on coincident marks do not overprint", () => {
    const ps = placeLabels(
      [
        { x: 100, y: 100, w: 30, h: 10 },
        { x: 100, y: 100, w: 30, h: 10 },
        { x: 101, y: 101, w: 30, h: 10 },
      ],
      { w: 300, h: 300 },
    );
    expect(overlap(ps[0], ps[1])).toBe(false);
    expect(overlap(ps[0], ps[2])).toBe(false);
    expect(overlap(ps[1], ps[2])).toBe(false);
  });
  it("flips left at the right edge and stays inside the box", () => {
    const [p] = placeLabels([{ x: 195, y: 50, w: 30, h: 10 }], { w: 200, h: 200 });
    expect(p.x + p.w).toBeLessThanOrEqual(200);
    expect(p.x).toBeGreaterThanOrEqual(0);
  });
});
