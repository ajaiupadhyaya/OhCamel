import { describe, expect, it } from "vitest";
import { EXECUTION_CAVEAT, frontierXY } from "./execution";

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
  it("says the inputs are assumptions and that epsilon is not included", () => {
    expect(EXECUTION_CAVEAT).toMatch(/assumptions, not estimates/);
    expect(EXECUTION_CAVEAT).toMatch(/not in them/);
  });
});
