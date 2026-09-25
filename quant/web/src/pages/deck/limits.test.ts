import { describe, expect, test } from "vitest";
import { DEFAULT_LIMITS, STORAGE_KEY, isMoneyKind, loadLimits, sanitizeLimits, saveLimits, toWire, uniqueName } from "./limits";

function memory() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    m,
  };
}

describe("limits", () => {
  test("defaults are the spec's table", () => {
    expect(DEFAULT_LIMITS.map((l) => [l.name, l.kind, l.threshold])).toEqual([
      ["gross-cap", "gross", 1.5],
      ["net-cap", "net", 1.1],
      ["name-cap", "name", 0.45],
      ["var-cap", "var", 0.02],
      ["es-cap", "es", 0.03],
      ["dd-cap", "drawdown", 0.15],
      ["day-loss", "day_loss", 0.015],
    ]);
    expect(DEFAULT_LIMITS.filter((l) => isMoneyKind(l.kind)).map((l) => l.name)).toEqual(["var-cap", "es-cap", "day-loss"]);
  });

  test("sanitize drops junk, duplicates and unknown kinds; tickers only on name limits", () => {
    const s = sanitizeLimits([
      { name: " a ", kind: "gross", threshold: "1.2" },
      { name: "a", kind: "net", threshold: 1 },
      { name: "b", kind: "bogus", threshold: 1 },
      { name: "c", kind: "var", threshold: -1 },
      { name: "d", kind: "name", threshold: 0.3, ticker: " spy " },
      { name: "e", kind: "gross", threshold: 1, ticker: "SPY" },
      null,
      "x",
    ]);
    expect(s).toEqual([
      { name: "a", kind: "gross", threshold: 1.2 },
      { name: "d", kind: "name", threshold: 0.3, ticker: "SPY" },
      { name: "e", kind: "gross", threshold: 1 },
    ]);
    expect(sanitizeLimits({})).toBe(null);
  });

  test("the wire: null until customised; a ticker only where it applies", () => {
    expect(toWire(null)).toBe(null);
    expect(toWire([{ name: "n", kind: "name", threshold: 0.3, ticker: "QQQ" }, { name: "g", kind: "gross", threshold: 1, ticker: "QQQ" }])).toEqual([
      { name: "n", kind: "name", threshold: 0.3, ticker: "QQQ" },
      { name: "g", kind: "gross", threshold: 1 },
    ]);
  });

  test("storage round-trips, and reset removes the key", () => {
    const st = memory();
    expect(loadLimits(st)).toBe(null);
    saveLimits(DEFAULT_LIMITS, st);
    expect(loadLimits(st)).toEqual(DEFAULT_LIMITS);
    saveLimits(null, st);
    expect(st.m.has(STORAGE_KEY)).toBe(false);
  });

  test("broken storage is survived", () => {
    const bad = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("quota"); }, removeItem: () => { throw new Error("denied"); } };
    expect(loadLimits(bad)).toBe(null);
    expect(() => saveLimits(DEFAULT_LIMITS, bad)).not.toThrow();
    const st = memory();
    st.setItem(STORAGE_KEY, "{not json");
    expect(loadLimits(st)).toBe(null);
  });

  test("unique names", () => {
    expect(uniqueName("cap", [])).toBe("cap");
    expect(uniqueName("cap", ["cap", "cap-2"])).toBe("cap-3");
  });
});
