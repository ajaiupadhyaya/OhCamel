import { describe, expect, it } from "vitest";
import { groupReasons, namesShort, reasonCode, reasonLabel, reasonNames } from "./reasons";

const FIX = "offline mode and no committed fixture (available: GLD, IEF, IWM, QQQ, SPY, TLT, XLE, XLF, XLK)";

describe("reasonCode (a vendor or Python error as a terse label)", () => {
  it.each([
    [`AAPL: ${FIX}`, "OFFLINE · NO FIXTURE"],
    ["offline mode: FRED series not in fixtures: ['DGS1MO', 'DGS3MO']", "OFFLINE · NO FIXTURE"],
    ["offline mode: SEC filings are fetched online only", "OFFLINE"],
    ["812 common live sessions (< 1512 for walk-forward)", "SHORT HISTORY"],
    ["2 tickers with full history over the span (< 3)", "SHORT HISTORY"],
    ["non-finite variance forecast from a degenerate fit; not scored", "DEGENERATE FIT"],
    ["model confidence set not computable: singular matrix", "MCS NOT COMPUTABLE"],
    ["Tiingo HTTP 429 Too Many Requests", "RATE LIMITED"],
    ["read timed out after 30s", "TIMEOUT"],
    ["FRED returned HTTP 500", "VENDOR ERROR"],
    ["something nobody planned for", "OTHER"],
  ])("%s -> %s", (r, code) => expect(reasonCode(r)).toBe(code));
  it("null reads as a dash", () => expect(reasonCode(null)).toBe("—"));
});

describe("reasonNames", () => {
  it("reads TICKER: prefixes joined by ;", () => expect(reasonNames(`DIA: ${FIX}; MDY: ${FIX}`)).toEqual(["DIA", "MDY"]));
  it("reads a Python list repr", () => expect(reasonNames("offline mode: FRED series not in fixtures: ['DGS1MO', 'DGS3MO', 'DGS6MO']")).toEqual(["DGS1MO", "DGS3MO", "DGS6MO"]));
  it("reads a plain list after fixtures:", () => expect(reasonNames("offline mode: FRED series not in fixtures: DGS1MO, DGS3MO")).toEqual(["DGS1MO", "DGS3MO"]));
  it("names nothing otherwise", () => expect(reasonNames("812 common live sessions (< 1512)")).toEqual([]));
});

describe("reasonLabel", () => {
  it("adds the count of names when there are several", () => expect(reasonLabel("offline mode: FRED series not in fixtures: ['DGS1MO', 'DGS3MO']")).toBe("OFFLINE · NO FIXTURE · 2 SERIES"));
  it("keeps an unknown reason verbatim", () => expect(reasonLabel("hostd_unavailable")).toBe("hostd_unavailable"));
});

describe("groupReasons (one row per reason, with a ticker count)", () => {
  it("collapses per-ticker rows", () => {
    const g = groupReasons([
      { ticker: "A", reason: `A: ${FIX}` },
      { ticker: "AAPL", reason: `AAPL: ${FIX}` },
      { ticker: "SPY", model: "garch", reason: "non-finite variance forecast from a degenerate fit; not scored" },
    ]);
    expect(g.map((x) => [x.code, x.model, x.n, x.tickers])).toEqual([
      ["OFFLINE · NO FIXTURE", null, 2, ["A", "AAPL"]],
      ["DEGENERATE FIT", "garch", 1, ["SPY"]],
    ]);
  });
});

describe("namesShort", () => {
  it("three then a count", () => expect(namesShort(["A", "B", "C", "D", "E"])).toBe("A, B, C +2"));
  it("dash when empty", () => expect(namesShort([])).toBe("—"));
});
