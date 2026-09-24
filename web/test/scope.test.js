// web/test/scope.test.js -- Figure 2's layout, the part of it that is arithmetic.
//
// Run with `make web-test` (node --test web/test/*.test.js; CI's lint job). The drawing
// is not tested here -- it is checked on the page -- but every number the
// drawing is handed comes out of layout(), and layout() touches no DOM.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const Scope = require("../scope.js");

// A limit as the frame carries it. excess is observed - threshold, so it is
// negative while there is room and positive once breached.
function lim(name, utilisation, opts) {
  const o = opts || {};
  const threshold = o.threshold === undefined ? 100000 : o.threshold;
  const observed = utilisation * threshold;
  return {
    name: name,
    unit: o.unit || "money",
    threshold: threshold,
    observed: observed,
    excess: observed - threshold,
    breached: o.breached === undefined ? utilisation > 1 : o.breached,
    utilisation: utilisation
  };
}
const never = function () { return false; };

test("depth is the headroom, clamped to [0, 1]", () => {
  assert.equal(Scope.depthOf(0), 1);
  assert.equal(Scope.depthOf(0.5), 0.5);
  assert.equal(Scope.depthOf(1), 0);
  assert.equal(Scope.depthOf(1.7), 0);
  assert.equal(Scope.depthOf(-0.2), 1);
});

test("scale is 1 / (1 + 5d): full size at the screen, a sixth at the far end", () => {
  assert.equal(Scope.scaleOf(0), 1);
  assert.ok(Math.abs(Scope.scaleOf(1) - 1 / 6) < 1e-12);
  assert.ok(Math.abs(Scope.scaleOf(0.5) - 1 / 3.5) < 1e-12);
});

test("the target is the fullest limit still ahead", () => {
  const m = Scope.layout([lim("a", 0.4), lim("b", 0.93), lim("c", 1.6), lim("d", 0.2)], [], never);
  assert.equal(m.target, "b");
  assert.deepEqual(m.gates.map((g) => g.name), ["d", "a", "b"]); // far to near: draw order
  assert.equal(m.gates.filter((g) => g.target).length, 1);
});

test("a tie goes to the first name, whatever order the book lists them", () => {
  const m = Scope.layout([lim("zeta", 0.8), lim("alpha", 0.8)], [], never);
  assert.equal(m.target, "alpha");
});

test("the readout is the target's dollars to spare, zero-padded to six", () => {
  // book-cap on the demo: 372,491.93 of 400,000
  const l = { name: "book-cap", unit: "money", observed: 372491.93, threshold: 400000,
              excess: -27508.07, breached: false, utilisation: 0.9312298 };
  const r = Scope.layout([l], [], never).readout;
  assert.equal(r.digits, "027508");
  assert.equal(r.unit, "dollars");
  assert.equal(r.over, false);
  assert.equal(r.caption, "book-cap · dollars to spare");
});

test("a dollar figure longer than six digits keeps every digit", () => {
  const r = Scope.layout([lim("big", 0.5, { threshold: 4000000 })], [], never).readout;
  assert.equal(r.digits, "2000000");
});

test("a fraction limit reads in basis points, zero-padded to four", () => {
  // 1.16% drawn down of a 2% cap: 0.84% = 84 bp to spare
  const l = { name: "dd-cap", unit: "fraction", observed: 0.0116, threshold: 0.02,
              excess: -0.0084, breached: false, utilisation: 0.58 };
  const r = Scope.layout([l], [], never).readout;
  assert.equal(r.digits, "0084");
  assert.equal(r.unit, "bp");
  assert.equal(r.caption, "dd-cap · bp to spare");
});

test("with every limit breached the readout is the worst breach, over", () => {
  const m = Scope.layout([lim("a", 1.2), lim("b", 5.7)], [], never);
  assert.equal(m.target, null);
  assert.equal(m.readout.over, true);
  assert.equal(m.readout.digits, "470000");
  assert.equal(m.readout.caption, "b · dollars over");
});

test("with nothing evaluable the readout is dashes, and says why", () => {
  const m = Scope.layout([], ["dd-cap"], never);
  assert.equal(m.readout.digits, "------");
  assert.equal(m.readout.caption, "no limit can be evaluated");
  const empty = Scope.layout([], [], never);
  assert.equal(empty.readout.caption, "this book has no limits");
});

test("breaches become rails, worst outermost, capped with the rest counted", () => {
  const ls = [1.1, 2.0, 1.5, 3.0, 1.2, 1.3, 1.4, 4.0].map((u, i) => lim("x" + i, u));
  const m = Scope.layout(ls, [], never);
  assert.equal(m.rails.length, Scope.MAX_RAILS);
  assert.equal(m.rails[0].name, "x7");
  assert.equal(m.rails[0].label, "x7 +300%");
  assert.equal(m.railsHidden, 2);
  assert.equal(m.gates.length, 0);
});

test("a breach just past the line keeps a decimal, so it never reads +0%", () => {
  const m = Scope.layout([lim("a", 1.003), lim("b", 1.094)], [], never);
  assert.deepEqual(m.rails.map((r) => r.label), ["b +9.4%", "a +0.3%"]);
});

test("an unevaluated limit is never given a depth", () => {
  const m = Scope.layout([lim("a", 0.5)], ["dd-cap"], never);
  assert.deepEqual(m.unknown, ["dd-cap"]);
  assert.ok(m.gates.every((g) => g.name !== "dd-cap"));
  const lamp = m.lamps.find((x) => x.name === "dd-cap");
  assert.equal(lamp.state, "unknown");
  assert.equal(lamp.pct, "?");
});

test("stale limits are marked, and a stale target dims the readout", () => {
  const stale = (n) => n === "limit:b";
  const m = Scope.layout([lim("a", 0.4), lim("b", 0.9)], [], stale);
  assert.equal(m.gates.find((g) => g.name === "b").stale, true);
  assert.equal(m.gates.find((g) => g.name === "a").stale, false);
  assert.equal(m.readout.stale, true);
  assert.equal(m.readout.caption, "b · dollars to spare · stale");
});

test("lamps keep the book's order and carry the exact utilisation", () => {
  const m = Scope.layout([lim("b", 0.931), lim("a", 1.64)], ["c"], never);
  assert.deepEqual(m.lamps.map((x) => [x.name, x.state, x.pct]),
    [["b", "ahead", "93%"], ["a", "over", "164%"], ["c", "unknown", "?"]]);
});

test("a non-finite utilisation is a breach with no percentage, not a crash", () => {
  // A zero threshold with a non-zero observation: Limits.utilisation is
  // infinity, which JSON carries as null.
  const l = { name: "z", unit: "money", observed: 5, threshold: 0, excess: 5,
              breached: true, utilisation: null };
  const m = Scope.layout([l], [], never);
  assert.equal(m.rails.length, 1);
  assert.equal(m.rails[0].label, "z over");
  assert.equal(m.lamps[0].pct, "—");
});

test("a non-finite utilisation that is not breached is placed at the screen, not hidden", () => {
  const l = { name: "z", unit: "money", observed: 0, threshold: 0, excess: 0,
              breached: false, utilisation: null };
  const m = Scope.layout([l], [], never);
  assert.equal(m.gates[0].depth, 0);
});

test("the summary a screen reader hears names the target and the breaches", () => {
  const m = Scope.layout([lim("a", 0.9), lim("b", 1.5)], ["c"], never);
  assert.equal(m.summary,
    "Nearest limit a, $10,000 to spare. Breached: b. Cannot be evaluated: c.");
});
