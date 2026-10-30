import { describe, expect, it } from "vitest";
import { escapeAction, tourDir } from "./keys";

const quiet = { overlay: false, handled: false, typing: false, replaying: false, selected: false };

describe("escapeAction: the topmost thing, and only that", () => {
  it("leaves an Esc to whatever is open above the page", () => {
    expect(escapeAction({ ...quiet, overlay: true, selected: true })).toBe("none");
    expect(escapeAction({ ...quiet, overlay: true, replaying: true })).toBe("none");
  });

  it("leaves an Esc something above the page has handled", () => {
    expect(escapeAction({ ...quiet, handled: true, selected: true, replaying: true })).toBe("none");
  });

  it("leaves an Esc that belongs to a field", () => {
    expect(escapeAction({ ...quiet, typing: true, selected: true })).toBe("none");
  });

  it("stops a replay before it touches the selection", () => {
    expect(escapeAction({ ...quiet, replaying: true, selected: true })).toBe("stop-replay");
  });

  it("deselects when nothing is above the selection", () => {
    expect(escapeAction({ ...quiet, selected: true })).toBe("deselect");
  });

  it("does nothing with nothing to close", () => {
    expect(escapeAction(quiet)).toBe("none");
  });
});

describe("tourDir", () => {
  it("moves on the arrows and the page keys", () => {
    expect(["ArrowDown", "PageDown"].map(tourDir)).toEqual([1, 1]);
    expect(["ArrowUp", "PageUp"].map(tourDir)).toEqual([-1, -1]);
  });

  it("leaves every other key alone", () => {
    expect([" ", "Enter", "ArrowLeft", "j"].map(tourDir)).toEqual([0, 0, 0, 0]);
  });
});
