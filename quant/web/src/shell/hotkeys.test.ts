import { describe, expect, it } from "vitest";
import { CHORD_MS, chordStep, IDLE, isTypingTarget, type ChordState } from "./hotkeys";

/** Feed a sequence of [key, ms] presses; return every path the machine produced. */
function run(presses: [string, number][]): (string | null)[] {
  let s: ChordState = IDLE;
  return presses.map(([k, t]) => {
    const r = chordStep(s, k, t);
    s = r.state;
    return r.path;
  });
}

describe("chordStep", () => {
  it("g then m within the window goes to markets", () => {
    expect(run([["g", 0], ["m", 400]])).toEqual([null, "/markets"]);
  });
  it("g then a pause past the window then m does nothing", () => {
    expect(run([["g", 0], ["m", 1200]])).toEqual([null, null]);
  });
  it("the window is 1000 ms, inclusive", () => {
    expect(CHORD_MS).toBe(1000);
    expect(run([["g", 0], ["r", 1000]])).toEqual([null, "/risk"]);
  });
  it("every chord in the key map", () => {
    const go = (k: string) => run([["g", 0], [k, 10]])[1];
    expect([go("m"), go("r"), go("p"), go("d"), go("s")]).toEqual(["/markets", "/risk", "/portfolio", "/deck", "/system"]);
  });
  it("a letter without g does nothing; an unknown second key cancels the chord", () => {
    expect(run([["m", 0]])).toEqual([null]);
    expect(run([["g", 0], ["x", 100], ["m", 200]])).toEqual([null, null, null]);
  });
  it("g is consumed and the chord resets after it fires", () => {
    let s: ChordState = IDLE;
    const a = chordStep(s, "g", 0);
    expect(a.consumed).toBe(true);
    s = a.state;
    const b = chordStep(s, "p", 50);
    expect(b).toMatchObject({ path: "/portfolio", consumed: true });
    expect(b.state).toEqual(IDLE);
    expect(chordStep(b.state, "p", 60).path).toBeNull();
  });
  it("is case-sensitive to shifted letters only by ignoring them", () => {
    expect(run([["G", 0], ["M", 100]])).toEqual([null, null]);
  });
});

describe("isTypingTarget", () => {
  it("inputs, textareas, selects and contenteditable are typing targets", () => {
    expect(isTypingTarget({ tagName: "INPUT" })).toBe(true);
    expect(isTypingTarget({ tagName: "TEXTAREA" })).toBe(true);
    expect(isTypingTarget({ tagName: "SELECT" })).toBe(true);
    expect(isTypingTarget({ tagName: "DIV", isContentEditable: true })).toBe(true);
    expect(isTypingTarget({ tagName: "BODY" })).toBe(false);
    expect(isTypingTarget(null)).toBe(false);
  });
});
