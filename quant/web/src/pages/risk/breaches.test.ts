import { describe, expect, it } from "vitest";
import { splitBreaches } from "./breaches";

describe("splitBreaches", () => {
  it("a breach is a loss beyond that morning's VaR; days without a forecast are not breaches", () => {
    const s = splitBreaches([0.01, -0.03, -0.01, -0.05], [0.02, 0.02, 0.02, null]);
    expect(s.ok).toEqual([0.01, null, -0.01, -0.05]);
    expect(s.breach).toEqual([null, -0.03, null, null]);
    expect(s.count).toBe(1);
  });
});
