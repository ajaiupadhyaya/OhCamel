// Figure 2's layout, the part of it that is arithmetic — ported from web/test/scope.test.js
// (branch feature/scope). The drawing is not tested here; every number it is handed comes
// out of layout(), and layout() touches no DOM.
import { describe, expect, test } from "vitest";
import * as Scope from "./scope";
import type { ScopeLimit } from "./scope";

// A limit as the frame carries it. excess is observed - threshold, so it is negative while
// there is room and positive once breached.
function lim(name: string, utilisation: number, o: { threshold?: number; unit?: string; breached?: boolean } = {}): ScopeLimit {
  const threshold = o.threshold === undefined ? 100000 : o.threshold;
  const observed = utilisation * threshold;
  return {
    name,
    unit: o.unit || "money",
    threshold,
    observed,
    excess: observed - threshold,
    breached: o.breached === undefined ? utilisation > 1 : o.breached,
    utilisation,
  };
}
const never = () => false;

describe("scope layout", () => {
  test("depth is the headroom, clamped to [0, 1]", () => {
    expect(Scope.depthOf(0)).toBe(1);
    expect(Scope.depthOf(0.5)).toBe(0.5);
    expect(Scope.depthOf(1)).toBe(0);
    expect(Scope.depthOf(1.7)).toBe(0);
    expect(Scope.depthOf(-0.2)).toBe(1);
  });

  test("scale is 1 / (1 + 5d): full size at the screen, a sixth at the far end", () => {
    expect(Scope.scaleOf(0)).toBe(1);
    expect(Math.abs(Scope.scaleOf(1) - 1 / 6)).toBeLessThan(1e-12);
    expect(Math.abs(Scope.scaleOf(0.5) - 1 / 3.5)).toBeLessThan(1e-12);
  });

  test("the target is the fullest limit still ahead", () => {
    const m = Scope.layout([lim("a", 0.4), lim("b", 0.93), lim("c", 1.6), lim("d", 0.2)], [], never);
    expect(m.target).toBe("b");
    expect(m.gates.map((g) => g.name)).toEqual(["d", "a", "b"]); // far to near: draw order
    expect(m.gates.filter((g) => g.target).length).toBe(1);
  });

  test("a tie goes to the first name, whatever order the book lists them", () => {
    const m = Scope.layout([lim("zeta", 0.8), lim("alpha", 0.8)], [], never);
    expect(m.target).toBe("alpha");
  });

  test("the readout is the target's dollars to spare, zero-padded to six", () => {
    // book-cap on the demo: 372,491.93 of 400,000
    const l: ScopeLimit = { name: "book-cap", unit: "money", observed: 372491.93, threshold: 400000, excess: -27508.07, breached: false, utilisation: 0.9312298 };
    const r = Scope.layout([l], [], never).readout;
    expect(r.digits).toBe("027508");
    expect(r.unit).toBe("dollars");
    expect(r.over).toBe(false);
    expect(r.caption).toBe("book-cap · dollars to spare");
  });

  test("a dollar figure longer than six digits keeps every digit", () => {
    const r = Scope.layout([lim("big", 0.5, { threshold: 4000000 })], [], never).readout;
    expect(r.digits).toBe("2000000");
  });

  test("a fraction limit reads in basis points, zero-padded to four", () => {
    // 1.16% drawn down of a 2% cap: 0.84% = 84 bp to spare
    const l: ScopeLimit = { name: "dd-cap", unit: "fraction", observed: 0.0116, threshold: 0.02, excess: -0.0084, breached: false, utilisation: 0.58 };
    const r = Scope.layout([l], [], never).readout;
    expect(r.digits).toBe("0084");
    expect(r.unit).toBe("bp");
    expect(r.caption).toBe("dd-cap · bp to spare");
  });

  test("with every limit breached the readout is the worst breach, over", () => {
    const m = Scope.layout([lim("a", 1.2), lim("b", 5.7)], [], never);
    expect(m.target).toBe(null);
    expect(m.readout.over).toBe(true);
    expect(m.readout.digits).toBe("470000");
    expect(m.readout.caption).toBe("b · dollars over");
  });

  test("with nothing evaluable the readout is dashes, and says why", () => {
    const m = Scope.layout([], ["dd-cap"], never);
    expect(m.readout.digits).toBe("------");
    expect(m.readout.caption).toBe("no limit can be evaluated");
    const empty = Scope.layout([], [], never);
    expect(empty.readout.caption).toBe("this book has no limits");
  });

  test("breaches become rails, worst outermost, capped with the rest counted", () => {
    const ls = [1.1, 2.0, 1.5, 3.0, 1.2, 1.3, 1.4, 4.0].map((u, i) => lim(`x${i}`, u));
    const m = Scope.layout(ls, [], never);
    expect(m.rails.length).toBe(Scope.MAX_RAILS);
    expect(m.rails[0].name).toBe("x7");
    expect(m.rails[0].label).toBe("x7 +300%");
    expect(m.railsHidden).toBe(2);
    expect(m.gates.length).toBe(0);
  });

  test("a breach just past the line keeps a decimal, so it never reads +0%", () => {
    const m = Scope.layout([lim("a", 1.003), lim("b", 1.094)], [], never);
    expect(m.rails.map((r) => r.label)).toEqual(["b +9.4%", "a +0.3%"]);
  });

  test("an unevaluated limit is never given a depth", () => {
    const m = Scope.layout([lim("a", 0.5)], ["dd-cap"], never);
    expect(m.unknown).toEqual(["dd-cap"]);
    expect(m.gates.every((g) => g.name !== "dd-cap")).toBe(true);
    const lamp = m.lamps.find((x) => x.name === "dd-cap")!;
    expect(lamp.state).toBe("unknown");
    expect(lamp.pct).toBe("?");
  });

  test("stale limits are marked, and a stale target dims the readout", () => {
    const stale = (n: string) => n === "limit:b";
    const m = Scope.layout([lim("a", 0.4), lim("b", 0.9)], [], stale);
    expect(m.gates.find((g) => g.name === "b")!.stale).toBe(true);
    expect(m.gates.find((g) => g.name === "a")!.stale).toBe(false);
    expect(m.readout.stale).toBe(true);
    expect(m.readout.caption).toBe("b · dollars to spare · stale");
  });

  test("lamps keep the book's order and carry the exact utilisation", () => {
    const m = Scope.layout([lim("b", 0.931), lim("a", 1.64)], ["c"], never);
    expect(m.lamps.map((x) => [x.name, x.state, x.pct])).toEqual([
      ["b", "ahead", "93%"],
      ["a", "over", "164%"],
      ["c", "unknown", "?"],
    ]);
  });

  test("a non-finite utilisation is a breach with no percentage, not a crash", () => {
    // A zero threshold with a non-zero observation: utilisation is infinity, which JSON
    // carries as null.
    const l: ScopeLimit = { name: "z", unit: "money", observed: 5, threshold: 0, excess: 5, breached: true, utilisation: null };
    const m = Scope.layout([l], [], never);
    expect(m.rails.length).toBe(1);
    expect(m.rails[0].label).toBe("z over");
    expect(m.lamps[0].pct).toBe("—");
  });

  test("a non-finite utilisation that is not breached is placed at the screen, not hidden", () => {
    const l: ScopeLimit = { name: "z", unit: "money", observed: 0, threshold: 0, excess: 0, breached: false, utilisation: null };
    const m = Scope.layout([l], [], never);
    expect(m.gates[0].depth).toBe(0);
  });

  test("the summary a screen reader hears names the target and the breaches", () => {
    const m = Scope.layout([lim("a", 0.9), lim("b", 1.5)], ["c"], never);
    expect(m.summary).toBe("Nearest limit a, $10,000 to spare. Breached: b. Cannot be evaluated: c.");
  });

  // Additions for the port: nulls where the JS took undefined.
  test("null inputs are an empty book, and the stale predicate is optional", () => {
    const m = Scope.layout(null, null);
    expect(m.readout.caption).toBe("this book has no limits");
    expect(m.lamps).toEqual([]);
  });

  test("a USD-unit limit (the engine's) reads in dollars", () => {
    const r = Scope.layout([lim("gross", 0.25, { unit: "USD", threshold: 1000 })], []).readout;
    expect(r.unit).toBe("dollars");
    expect(r.digits).toBe("000750");
  });
});
