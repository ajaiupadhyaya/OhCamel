/**
 * The wheel on the tour (spec section 6.7): enough scroll takes one step, and a step locks the wheel for a moment, so
 * a flick of a trackpad is one step and not eight. Pure, no DOM; the page brings the event and the clock.
 */
export const WHEEL_STEP_PX = 60;
export const WHEEL_LOCK_MS = 900;

export interface WheelState {
  /** The delta gathered since the last step. */
  acc: number;
  /** No step before this time, in ms. */
  until: number;
}

export const WHEEL_IDLE: WheelState = { acc: 0, until: 0 };

/** One wheel event at `now` ms: the state after it, and the step it takes (1 forward, -1 back, 0 none). What arrives during the lock is dropped. */
export function wheelStep(s: WheelState, deltaY: number, now: number): { state: WheelState; dir: -1 | 0 | 1 } {
  if (now < s.until) return { state: { acc: 0, until: s.until }, dir: 0 };
  const acc = s.acc + deltaY;
  if (Math.abs(acc) <= WHEEL_STEP_PX) return { state: { acc, until: s.until }, dir: 0 };
  return { state: { acc: 0, until: now + WHEEL_LOCK_MS }, dir: acc > 0 ? 1 : -1 };
}
