import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { atMostEvery, POLL_DOWN_MS, POLL_MS, REFETCH_GAP_MS, RETRY_MS, watchJobs } from "./useMachine";

/** Just enough of an EventSource: what watchJobs touches, and a way to drive it. */
class FakeSource {
  static all: FakeSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, (m: { data: string }) => void>();
  constructor(readonly url: string) {
    FakeSource.all.push(this);
  }
  addEventListener(type: string, fn: (m: { data: string }) => void) {
    this.listeners.set(type, fn);
  }
  close() {
    this.closed = true;
  }
  job(data: string) {
    this.listeners.get("job")?.({ data });
  }
}
const latest = () => FakeSource.all[FakeSource.all.length - 1];
const on = () => ({ job: vi.fn(), up: vi.fn(), down: vi.fn() });
const JOB = '{"type":"job","id":"J1","state":"running","progress":0.5,"message":"covariance"}';

beforeEach(() => {
  FakeSource.all = [];
  vi.useFakeTimers();
  vi.stubGlobal("EventSource", FakeSource);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("the timings", () => {
  it("polls every five minutes, every minute when the stream is down, and tries the stream again after five", () => {
    expect([POLL_MS, POLL_DOWN_MS, RETRY_MS]).toEqual([300_000, 60_000, 300_000]);
    expect(REFETCH_GAP_MS).toBe(30_000);
  });
});

describe("watchJobs", () => {
  it("opens the job stream once and hands on each job event it can read", () => {
    const h = on();
    watchJobs(h);
    expect(FakeSource.all.map((s) => s.url)).toEqual(["/api/jobs/events"]);
    latest().job(JOB);
    latest().job("not json");
    latest().job('{"id":"J1","state":"paused"}');
    expect(h.job).toHaveBeenCalledTimes(1);
    expect(h.job).toHaveBeenCalledWith({ type: "job", id: "J1", state: "running", progress: 0.5, message: "covariance" });
  });

  it("says the stream is up when it opens", () => {
    const h = on();
    watchJobs(h);
    latest().onopen?.();
    expect(h.up).toHaveBeenCalledTimes(1);
    expect(h.down).not.toHaveBeenCalled();
  });

  it("on an error closes the stream, says it is down, and opens a new one only after five minutes", () => {
    const h = on();
    watchJobs(h);
    const first = latest();
    first.onerror?.();
    expect(first.closed).toBe(true);
    expect(h.down).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(RETRY_MS - 1);
    expect(FakeSource.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSource.all).toHaveLength(2);
    latest().onopen?.();
    expect(h.up).toHaveBeenCalledTimes(1);
    latest().job(JOB);
    expect(h.job).toHaveBeenCalledTimes(1);
  });

  it("keeps trying, five minutes apart, while the stream keeps failing", () => {
    const h = on();
    watchJobs(h);
    for (let i = 1; i <= 3; i++) {
      latest().onerror?.();
      vi.advanceTimersByTime(RETRY_MS);
      expect(FakeSource.all).toHaveLength(i + 1);
    }
    expect(FakeSource.all.slice(0, 3).every((s) => s.closed)).toBe(true);
    expect(h.down).toHaveBeenCalledTimes(3);
  });

  it("closes the stream when it is stopped", () => {
    const stop = watchJobs(on());
    stop();
    expect(latest().closed).toBe(true);
  });

  it("stopped while waiting to retry, never opens another", () => {
    const stop = watchJobs(on());
    latest().onerror?.();
    stop();
    vi.advanceTimersByTime(RETRY_MS * 2);
    expect(FakeSource.all).toHaveLength(1);
  });

  it("is down at once, and quiet, where there is no EventSource", () => {
    vi.unstubAllGlobals();
    vi.stubGlobal("EventSource", undefined);
    const h = on();
    const stop = watchJobs(h);
    expect(h.down).toHaveBeenCalledTimes(1);
    expect(FakeSource.all).toHaveLength(0);
    expect(() => stop()).not.toThrow();
  });
});

describe("atMostEvery", () => {
  it("lets the first call through, drops the ones inside the gap, and lets the next one after it through", () => {
    let t = 1_000_000;
    const fn = vi.fn();
    const once = atMostEvery(30_000, fn, () => t);
    once();
    t += 29_999;
    once();
    once();
    expect(fn).toHaveBeenCalledTimes(1);
    t += 1;
    once();
    expect(fn).toHaveBeenCalledTimes(2);
    t += 5_000;
    once();
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
