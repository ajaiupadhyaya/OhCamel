import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GraphDiagram } from "./GraphDiagram";

describe("GraphDiagram (Paper Tape: square nodes, no colour, terse labels)", () => {
  const html = renderToStaticMarkup(<GraphDiagram />);
  const rects = html.match(/<rect\b[^>]*>/g) ?? [];
  it("draws every node as a square-cornered rect", () => {
    expect(rects.length).toBe(19);
    for (const r of rects) {
      expect(r).toMatch(/\brx="0"/);
      expect(r).toMatch(/\bry="0"/);
    }
  });
  it("counts the SPY tick's recomputed nodes against the whole graph", () => {
    expect(html).toContain("8 / 14 RECOMPUTED · 6 REUSED");
  });
  it("has no gradient and no sentence-case control labels", () => {
    expect(html).not.toMatch(/gradient/i);
    expect(html).not.toMatch(/Send a tick|Trade \(qty\)|Change α/);
  });
});
