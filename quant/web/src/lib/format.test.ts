import { describe, expect, it } from "vitest";
import { fixedNum, fmtSci, roundTo } from "./format";

describe("fmtSci", () => {
  it("scientific notation with a typographic minus; em dash when missing; exact zero stays 0", () => {
    expect(fmtSci(0.000123, 2)).toBe("1.23e−4");
    expect(fmtSci(-4.5e-7, 1)).toBe("−4.5e−7");
    expect(fmtSci(0)).toBe("0");
    expect(fmtSci(null)).toBe("—");
    expect(fmtSci(Number.NaN)).toBe("—");
  });
});

describe("roundTo / fixedNum (the only home of toFixed)", () => {
  it("roundTo rounds to a number of decimals and returns a number", () => {
    expect(roundTo(0.1 + 0.2, 10)).toBe(0.3);
    expect(roundTo(-0.004, 2)).toBe(-0);
    expect(roundTo(1.23456, 4)).toBe(1.2346);
  });
  it("fixedNum is plain fixed decimals: no grouping, no sign glyph (SVG paths, input values)", () => {
    expect(fixedNum(1234.5, 1)).toBe("1234.5");
    expect(fixedNum(-2.346, 2)).toBe("-2.35");
    expect(fixedNum(3, 0)).toBe("3");
  });
});
