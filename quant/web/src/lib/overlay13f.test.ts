import { describe, expect, it } from "vitest";
import type { Option13F } from "./market";
import { OVERLAY_NAMES, optionOverlay, withOverlay } from "./overlay13f";

const opt = (ticker: string | null, put_call: string, weight_on_equity: number | null): Option13F => ({ ticker, issuer: ticker ?? "?", put_call, value_usd: 1, weight_on_equity });

describe("optionOverlay", () => {
  it("takes a call long and a put short at the assumed delta", () => {
    const o = optionOverlay([opt("AAA", "Call", 0.2), opt("BBB", "Put", 0.4)], 0.5);
    expect(o.holdings).toEqual([{ ticker: "BBB", weight: -0.2 }, { ticker: "AAA", weight: 0.1 }]);
  });
  it("nets a call and a put in one name, and drops a perfect net", () => {
    const o = optionOverlay([opt("AAA", "Call", 0.3), opt("AAA", "Put", 0.1), opt("BBB", "Call", 0.2), opt("BBB", "Put", 0.2)], 0.5);
    expect(o.holdings).toHaveLength(1);
    expect(o.holdings[0].ticker).toBe("AAA");
    expect(o.holdings[0].weight).toBeCloseTo(0.1);
  });
  it("counts the rows it cannot use rather than dropping them in silence", () => {
    expect(optionOverlay([opt(null, "Put", 0.2), opt("AAA", "Put", null)], 0.5)).toEqual({ holdings: [], left_out: 0, unmapped: 2 });
  });
  it("keeps the largest names and says how many it left out", () => {
    const many = Array.from({ length: OVERLAY_NAMES + 5 }, (_, i) => opt(`N${i}`, "Call", (i + 1) / 1000));
    const o = optionOverlay(many, 1);
    expect(o.holdings).toHaveLength(OVERLAY_NAMES);
    expect(o.left_out).toBe(5);
    expect(o.holdings[0].ticker).toBe(`N${OVERLAY_NAMES + 4}`);
  });
});

describe("withOverlay", () => {
  it("nets a name in both and keeps the stock side otherwise", () => {
    const out = withOverlay([{ ticker: "AAA", weight: 0.6 }, { ticker: "CCC", weight: 0.4 }], [{ ticker: "AAA", weight: -0.2 }, { ticker: "BBB", weight: -0.1 }]);
    expect(out).toEqual([{ ticker: "AAA", weight: expect.closeTo(0.4) }, { ticker: "CCC", weight: 0.4 }, { ticker: "BBB", weight: -0.1 }]);
  });
  it("drops a name the overlay nets to zero", () => {
    expect(withOverlay([{ ticker: "AAA", weight: 0.5 }], [{ ticker: "AAA", weight: -0.5 }])).toEqual([]);
  });
  it("does not rescale: the net exposure is the stock book's plus the overlay's", () => {
    const out = withOverlay([{ ticker: "AAA", weight: 1 }], [{ ticker: "BBB", weight: -0.25 }]);
    expect(out.reduce((s, h) => s + h.weight, 0)).toBeCloseTo(0.75);
  });
});
