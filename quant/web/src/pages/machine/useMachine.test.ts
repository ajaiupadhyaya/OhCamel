import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { atMostEvery, POLL_DOWN_MS, POLL_MS, REFETCH_GAP_MS, RETRY_MS, watchJobs } from "./useMachine";

/** Just enough of an EventSource: what watchJobs touches, and a way to drive it. */
class FakeSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static all: FakeSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readyState = FakeSource.CONNECTING;
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
    this.readyState = FakeSource.CLOSED;
  }
  job(data: string) {
    this.listeners.get("job")?.({ data });
  }
  /** The connection came up. */
  open() {
    this.readyState = FakeSource.OPEN;
    this.onopen?.();
  }
  /** The server ended the stream (its lifetime or idle limit): the browser reports an error and will reconnect. */
  ended() {
    this.readyState = FakeSource.CONNECTING;
    this.onerror?.();
  }
  /** The connection was refused, or its response was not an event stream: the browser gives up. */
  refused() {
    this.readyState = FakeSource.CLOSED;
    this.onerror?.();
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
    latest().open();
    expect(h.up).toHaveBeenCalledTimes(1);
    expect(h.down).not.toHaveBeenCalled();
  });

  // The server ends every stream at its lifetime (300 s) or idle limit (120 s). The browser reports that as an
  // error with readyState CONNECTING and reconnects by itself with Last-Event-ID (controller ruling 18).
  it("leaves a stream that opened alone when the server ends it: no close, no polling, no retry timer", () => {
    const h = on();
    watchJobs(h);
    const s = latest();
    s.open();
    s.ended();
    expect(s.closed).toBe(false);
    expect(h.down).not.toHaveBeenCalled();
    vi.advanceTimersByTime(RETRY_MS * 2);
    expect(FakeSource.all).toHaveLength(1);
  });

  it("takes the browser's own reconnections as the same stream: events flow and it is up again each time", () => {
    const h = on();
    watchJobs(h);
    const s = latest();
    for (let i = 0; i < 3; i++) {
      s.open();
      s.ended();
    }
    s.open();
    s.job(JOB);
    expect(h.up).toHaveBeenCalledTimes(4);
    expect(h.job).toHaveBeenCalledTimes(1);
    expect(h.down).not.toHaveBeenCalled();
    expect(FakeSource.all).toHaveLength(1);
  });

  it("gives up when the browser's reconnection fails: a second error with no open between", () => {
    const h = on();
    watchJobs(h);
    const s = latest();
    s.open();
    s.ended();
    s.ended();
    expect(s.closed).toBe(true);
    expect(h.down).toHaveBeenCalledTimes(1);
  });

  it("closes a stream that opened and then failed hard (readyState CLOSED), says it is down, and retries after five minutes", () => {
    const h = on();
    watchJobs(h);
    const first = latest();
    first.open();
    first.refused();
    expect(first.closed).toBe(true);
    expect(h.down).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(RETRY_MS - 1);
    expect(FakeSource.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSource.all).toHaveLength(2);
    latest().open();
    expect(h.up).toHaveBeenCalledTimes(2);
    latest().job(JOB);
    expect(h.job).toHaveBeenCalledTimes(1);
  });

  it("treats an error before the stream ever opened as a hard failure, even with readyState CONNECTING", () => {
    const h = on();
    watchJobs(h);
    const first = latest();
    first.ended();
    expect(first.closed).toBe(true);
    expect(h.down).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(RETRY_MS);
    expect(FakeSource.all).toHaveLength(2);
  });

  it("keeps trying, five minutes apart, while the stream keeps failing", () => {
    const h = on();
    watchJobs(h);
    for (let i = 1; i <= 3; i++) {
      latest().refused();
      vi.advanceTimersByTime(RETRY_MS);
      expect(FakeSource.all).toHaveLength(i + 1);
    }
    expect(FakeSource.all.slice(0, 3).every((s) => s.closed)).toBe(true);
    expect(h.down).toHaveBeenCalledTimes(3);
  });

  it("gives a retried stream that opens the same patience: the server's end is again left alone", () => {
    const h = on();
    watchJobs(h);
    latest().refused();
    vi.advanceTimersByTime(RETRY_MS);
    latest().open();
    latest().ended();
    expect(latest().closed).toBe(false);
    expect(h.down).toHaveBeenCalledTimes(1);
  });

  it("closes the stream when it is stopped, also after the server ended it", () => {
    const stop = watchJobs(on());
    latest().open();
    latest().ended();
    stop();
    expect(latest().closed).toBe(true);
  });

  it("stopped while waiting to retry, never opens another", () => {
    const stop = watchJobs(on());
    latest().refused();
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
