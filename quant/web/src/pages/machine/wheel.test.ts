import { describe, expect, it } from "vitest";
import { WHEEL_IDLE, WHEEL_LOCK_MS, WHEEL_STEP_PX, wheelStep } from "./wheel";

/** Feed a sequence of [deltaY, ms] events; return the direction each gave. */
function run(events: [number, number][]): number[] {
  let state = WHEEL_IDLE;
  return events.map(([d, t]) => {
    const r = wheelStep(state, d, t);
    state = r.state;
    return r.dir;
  });
}

describe("wheelStep", () => {
  it("steps forward on a scroll down, back on a scroll up", () => {
    expect(run([[100, 0]])).toEqual([1]);
    expect(run([[-100, 0]])).toEqual([-1]);
  });

  it("gathers small deltas until they pass the threshold", () => {
    expect(run([[25, 0], [25, 10], [11, 20]])).toEqual([0, 0, 1]);
  });

  it("is not moved by exactly the threshold", () => {
    expect(run([[WHEEL_STEP_PX, 0]])).toEqual([0]);
  });

  it("lets opposite directions cancel", () => {
    expect(run([[50, 0], [-50, 10], [50, 20]])).toEqual([0, 0, 0]);
  });

  it("takes one step per lock, however hard the flick", () => {
    expect(run([[500, 0], [500, 10], [500, WHEEL_LOCK_MS - 1]])).toEqual([1, 0, 0]);
  });

  it("drops what falls inside the lock, so a flick's tail is not a second step", () => {
    expect(run([[100, 0], [55, 500], [10, WHEEL_LOCK_MS]])).toEqual([1, 0, 0]);
  });

  it("steps again once the lock has passed", () => {
    expect(run([[100, 0], [100, WHEEL_LOCK_MS]])).toEqual([1, 1]);
  });

  it("holds its numbers: 60 px, 900 ms", () => {
    expect([WHEEL_STEP_PX, WHEEL_LOCK_MS]).toEqual([60, 900]);
  });
});
