import { describe, expect, it } from "vitest";
import { fmtSci } from "./format";

describe("fmtSci", () => {
  it("scientific notation with a typographic minus; em dash when missing; exact zero stays 0", () => {
    expect(fmtSci(0.000123, 2)).toBe("1.23e−4");
    expect(fmtSci(-4.5e-7, 1)).toBe("−4.5e−7");
    expect(fmtSci(0)).toBe("0");
    expect(fmtSci(null)).toBe("—");
    expect(fmtSci(Number.NaN)).toBe("—");
  });
});
