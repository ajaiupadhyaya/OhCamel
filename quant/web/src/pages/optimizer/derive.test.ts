import { describe, expect, it } from "vitest";
import { beatVerdict, frontierSlices, roundWeight, sharpeVerdicts, viewRows } from "./derive";
import type { CompareOut, FrontierPoint, OptimizeOut } from "./types";

const test = (diff: number | null, p: number | null, se = 0.1) => ({ ledoit_wolf: { test: "lw", z: null, p_value: p, sharpe_diff_annual: diff, se_annual: se, reference: "" } });
const cmp = (tests: Record<string, ReturnType<typeof test>>) =>
  ({
    stats: [{ method: "equal_weight", label: "1/N" }, ...Object.keys(tests).map((m) => ({ method: m, label: m.toUpperCase() }))],
    tests: Object.fromEntries(Object.entries(tests).map(([k, v]) => [k, { vs: "equal_weight", ...v }])),
  }) as unknown as CompareOut;

describe("sharpeVerdicts / beatVerdict (walk-forward vs 1/N)", () => {
  it("better and worse need p < 0.05 and the sign; the benchmark itself is not tested", () => {
    const v = sharpeVerdicts(cmp({ hrp: test(0.3, 0.01), min_variance: test(-0.4, 0.02), max_sharpe: test(0.2, 0.4) }));
    expect(v.map((x) => x.method)).toEqual(["hrp", "min_variance", "max_sharpe"]);
    expect(v.map((x) => [x.better, x.worse])).toEqual([
      [true, false],
      [false, true],
      [false, false],
    ]);
  });
  it("FAIL when nothing beats 1/N, PASS when one does, INSUFFICIENT DATA with no testable method", () => {
    expect(beatVerdict(sharpeVerdicts(cmp({ hrp: test(0.1, 0.3), herc: test(-0.4, 0.01) })))).toEqual({ value: "FAIL", better: 0, worse: 1, same: 1, n: 2 });
    expect(beatVerdict(sharpeVerdicts(cmp({ hrp: test(0.3, 0.01), herc: test(0.1, 0.3) }))).value).toBe("PASS");
    expect(beatVerdict(sharpeVerdicts(cmp({ hrp: test(null, null) }))).value).toBe("INSUFFICIENT DATA");
    expect(beatVerdict([]).value).toBe("INSUFFICIENT DATA");
  });
  it("Holm across the n tested methods: one p = 0.03 among 7 does not PASS, one p = 0.001 does", () => {
    const six = { a: test(0.05, 0.6), b: test(0.02, 0.7), c: test(-0.01, 0.9), d: test(0.04, 0.5), e: test(0.01, 0.8), f: test(-0.03, 0.4) };
    const weak = sharpeVerdicts(cmp({ hrp: test(0.3, 0.03), ...six }));
    expect(weak[0].p).toBe(0.03);
    expect(weak[0].pHolm).toBeCloseTo(0.21);
    expect(weak[0].better).toBe(false);
    expect(beatVerdict(weak)).toMatchObject({ value: "FAIL", better: 0, n: 7 });
    const strong = sharpeVerdicts(cmp({ hrp: test(0.3, 0.001), ...six }));
    expect(strong[0].pHolm).toBeCloseTo(0.007);
    expect(beatVerdict(strong)).toMatchObject({ value: "PASS", better: 1, n: 7 });
  });
  it("Holm is step-down and monotone; untestable rows carry no adjusted p", () => {
    const v = sharpeVerdicts(cmp({ hrp: test(0.3, 0.01), herc: test(0.2, 0.02), mv: test(-0.1, 0.04), x: test(null, null) }));
    expect(v.map((r) => r.pHolm)).toEqual([expect.closeTo(0.03), expect.closeTo(0.04), expect.closeTo(0.04), null]);
    expect(v.map((r) => [r.better, r.worse])).toEqual([[true, false], [true, false], [false, true], [false, false]]);
    expect(beatVerdict(v)).toMatchObject({ n: 3 });
  });
});

describe("frontierSlices", () => {
  const pts: FrontierPoint[] = Array.from({ length: 41 }, (_, i) => ({ vol: 0.05 + i * 0.005, ret: 0.02 + i * 0.002, weights: {} }));
  it("k points evenly over the vol range, first and last included, no repeats", () => {
    const s = frontierSlices(pts, 5);
    expect(s).toHaveLength(5);
    expect(s[0].vol).toBeCloseTo(0.05);
    expect(s[4].vol).toBeCloseTo(0.25);
    expect(s[2].vol).toBeCloseTo(0.15);
  });
  it("short frontiers come back whole", () => {
    expect(frontierSlices(pts.slice(0, 3), 7)).toHaveLength(3);
    expect(frontierSlices([], 7)).toEqual([]);
  });
});

describe("roundWeight", () => {
  it("rounds to six decimals without toFixed", () => {
    expect(roundWeight(0.123456789)).toBe(0.123457);
    expect(roundWeight(-0.0000004)).toBe(0);
  });
});

describe("viewRows (Black–Litterman views read back)", () => {
  it("posterior is long − short for relative views and excess + rf for absolute ones; pull is the share of the gap travelled", () => {
    const e = {
      params: { risk_free: 0.02 },
      posterior_excess: { GLD: 0.05, SPY: 0.06, TLT: 0.01 },
      views: [
        { label: "GLD 6%", kind: "absolute", long: "GLD", short: null, value: 0.08, confidence: 0.5, omega: 0.001, prior_implied: 0.06 },
        { label: "SPY > TLT", kind: "relative", long: "SPY", short: "TLT", value: 0.06, confidence: 0.5, omega: 0.001, prior_implied: 0.04 },
      ],
    } as unknown as OptimizeOut["expected_returns"];
    const r = viewRows(e);
    expect(r[0].posterior).toBeCloseTo(0.07);
    expect(r[0].pull).toBeCloseTo(0.5);
    expect(r[1].posterior).toBeCloseTo(0.05);
    expect(r[1].pull).toBeCloseTo(0.5);
  });
});
