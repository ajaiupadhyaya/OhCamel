import { describe, expect, it } from "vitest";
import { equalSlices, executionCaveat, frontierXY } from "./execution";

const row = (lam: number, expected_cost: number, std: number) => ({ lam, expected_cost, variance: std * std, std });

describe("frontierXY", () => {
  it("sorts by risk so the frontier is a curve, lowest risk first", () => {
    const f = frontierXY([row(0, 10, 9), row(1, 12, 5), row(2, 20, 1)]);
    expect(f.x).toEqual([1, 5, 9]);
    expect(f.y).toEqual([20, 12, 10]);
  });
  it("drops a point that is not finite rather than drawing it at zero", () => {
    const f = frontierXY([row(0, 10, 9), row(1, Number.NaN, 5)]);
    expect(f.x).toEqual([9]);
  });
  it("says the inputs are assumptions and that epsilon is not included, with the payload's sigma window", () => {
    expect(executionCaveat(60)).toMatch(/assumptions, not estimates/);
    expect(executionCaveat(60)).toMatch(/not in them/);
    expect(executionCaveat(60)).toMatch(/realised 60-day volatility/);
    expect(executionCaveat(null)).not.toMatch(/20-day/);
  });
  it("finds the lambda = 0 row by its value, not its position", () => {
    expect(equalSlices([row(2, 20, 1), row(0, 10, 9)])?.expected_cost).toBe(10);
    expect(equalSlices([row(1, 12, 5)])).toBeNull();
  });
});
