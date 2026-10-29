import { describe, expect, it } from "vitest";
import { applyJobEvent, parseJobEvent } from "./runs";
import type { JobEvent, Run } from "./types";

const run = (id: string, over: Partial<Run> = {}): Run => ({
  id, kind: "signals.daily", state: "running", start: "2026-10-28T22:45:01.221Z", end: null, cpu: null, progress: 0.2, msg: "farm_tsmom_rates", ...over,
});
const event = (id: string, over: Partial<JobEvent> = {}): JobEvent => ({ type: "job", id, state: "running", progress: 0.7, message: "farm_tsmom_credit", ...over });

describe("applyJobEvent", () => {
  it("patches state, progress and msg of the run the event names, and nothing else", () => {
    const runs = [run("A"), run("B", { kind: "signals.overlay", progress: 0.1, msg: null })];
    const out = applyJobEvent(runs, event("A"));
    expect(out.unknown).toBe(false);
    expect(out.runs[0]).toEqual({ ...runs[0], state: "running", progress: 0.7, msg: "farm_tsmom_credit" });
    expect(out.runs[1]).toBe(runs[1]);
  });

  it("does not touch the runs it was given", () => {
    const runs = [run("A")];
    const before = structuredClone(runs);
    applyJobEvent(runs, event("A", { progress: 0.9 }));
    expect(runs).toEqual(before);
  });

  it("moves a queued run to running without inventing a start", () => {
    const queued = run("Q", { state: "queued", start: null, progress: null, msg: null });
    const out = applyJobEvent([queued], event("Q", { progress: 0, message: null }));
    expect(out.runs[0]).toEqual({ ...queued, state: "running", progress: 0 });
    expect(out.runs[0].start).toBeNull();
  });

  it.each(["done", "failed", "cancelled"] as const)("keeps %s: the state is recorded, and no later event moves it", (state) => {
    const finished = applyJobEvent([run("A")], event("A", { state, progress: state === "done" ? 1 : null, message: null }));
    expect(finished.runs[0]).toMatchObject({ state, progress: state === "done" ? 1 : null, msg: null });
    for (const stale of [event("A", { state: "running", progress: 0.4 }), event("A", { state: "queued", progress: null }), event("A", { state: "done", progress: 1 })]) {
      const out = applyJobEvent(finished.runs, stale);
      expect(out).toEqual({ runs: finished.runs, unknown: false });
      expect(out.runs).toBe(finished.runs);
    }
  });

  it("lets a retried run go back to queued: that state is not final", () => {
    const out = applyJobEvent([run("A")], event("A", { state: "queued", progress: null, message: "retrying after: boom" }));
    expect(out.runs[0]).toMatchObject({ state: "queued", progress: null });
  });

  it("answers an unseen id with unknown: true and the very same runs", () => {
    const runs = [run("A")];
    const out = applyJobEvent(runs, event("Z"));
    expect(out.unknown).toBe(true);
    expect(out.runs).toBe(runs);
  });

  it("calls every id unknown while there are no runs", () => {
    const none: Run[] = [];
    const out = applyJobEvent(none, event("A"));
    expect(out).toEqual({ runs: none, unknown: true });
    expect(out.runs).toBe(none);
  });

  it("is idempotent: a duplicate event changes nothing, and says so by returning the same array", () => {
    const e = event("A", { progress: 0.55 });
    const once = applyJobEvent([run("A")], e);
    const twice = applyJobEvent(once.runs, e);
    expect(twice.runs).toBe(once.runs);
    expect(twice.runs).toEqual(once.runs);
    expect(twice.unknown).toBe(false);
  });
});

describe("parseJobEvent", () => {
  it("reads the /api/jobs/events data line", () => {
    expect(parseJobEvent('{"type":"job","id":"J1","state":"running","progress":0.4,"message":"covariance"}')).toEqual({
      type: "job", id: "J1", state: "running", progress: 0.4, message: "covariance",
    });
    expect(parseJobEvent('{"type":"job","id":"J1","state":"queued","progress":null,"message":null}')).toEqual({
      type: "job", id: "J1", state: "queued", progress: null, message: null,
    });
  });

  it("leaves out what it cannot trust rather than inventing it", () => {
    expect(parseJobEvent('{"id":"J1","state":"running","progress":"half","message":7}')).toEqual({
      type: "job", id: "J1", state: "running", progress: null, message: null,
    });
    expect(parseJobEvent('{"id":"J1","state":"done","progress":null}')?.message).toBeNull();
  });

  it.each([
    ["not json", "{"],
    ["a list", "[]"],
    ["null", "null"],
    ["no id", '{"state":"running"}'],
    ["an empty id", '{"id":"","state":"running"}'],
    ["a state the runs do not have", '{"id":"J1","state":"paused"}'],
    ["no state", '{"id":"J1"}'],
  ])("drops %s", (_what, data) => {
    expect(parseJobEvent(data)).toBeNull();
  });
});
