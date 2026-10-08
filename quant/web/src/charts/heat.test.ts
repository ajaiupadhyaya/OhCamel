import { describe, expect, it } from "vitest";
import { heatScale } from "./heat";

const theme = {
  seq: ["#e2ddd2", "#bdb6a6", "#8f8878", "#5e5b54", "#111111"],
  div: ["#c8201a", "#dd8178", "#f0c9c5", "#ece8df", "#d2cec4", "#8f8a7e", "#111111"],
  divNeutral: ["#c8201a", "#dd8178", "#f0c9c5", "#ece8df", "#d2cec4", "#8f8a7e", "#111111"],
};
const RED = new Set(["#c8201a", "#dd8178", "#f0c9c5", "rgb(200, 32, 26)", "rgb(221, 129, 120)", "rgb(240, 201, 197)"]);

describe("heatScale, neutral palette (correlations, weights): ink only", () => {
  const s = heatScale([-1, -0.4, 0, 0.4, 1], { diverging: true, zmid: 0, zmin: -1, zmax: 1, palette: "neutral" }, theme);
  it("a negative is the ink density of its magnitude, hatched; never red", () => {
    for (const v of [-1, -0.7, -0.4, -0.05]) {
      const neg = s.cell(v);
      expect(neg.hatch).toBe(true);
      expect(neg.fill).toBe(s.cell(-v).fill);
      expect(RED.has(neg.fill)).toBe(false);
    }
  });
  it("positives are not hatched, and darker with magnitude", () => {
    expect(s.cell(0.4).hatch).toBe(false);
    expect(s.cell(1).fill).toBe("rgb(17, 17, 17)");
  });
  it("the readout never marks a neutral negative in signal", () => expect(s.signal(-0.9)).toBe(false));
});

describe("heatScale, pnl palette (returns): losses in signal", () => {
  const s = heatScale([-0.1, 0.1], { diverging: true, zmid: 0, palette: "pnl" }, theme);
  it("a loss is red and not hatched", () => {
    expect(s.cell(-0.1).fill).toBe("rgb(200, 32, 26)");
    expect(s.cell(-0.1).hatch).toBe(false);
    expect(s.signal(-0.1)).toBe(true);
  });
});

describe("heatScale, sequential", () => {
  it("runs the ink density ramp", () => {
    const s = heatScale([0, 1], { palette: "pnl" }, theme);
    expect(s.div).toBe(false);
    expect(s.cell(1).fill).toBe("rgb(17, 17, 17)");
  });
});
