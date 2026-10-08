import { describe, expect, it } from "vitest";
import type { JobRow } from "./compute";
import {
  applyJobEvent,
  JOBS_POLL_DOWN,
  JOBS_POLL_LIVE,
  JOBS_POLL_ERROR,
  jobsPollInterval,
  MC_MAX_AGE_S,
  mcIsStale,
  mcLabel,
  mcVarOf,
  parseJobEvent,
  readingInterval,
  reconnectDelay,
} from "./live";

const run = (id: string, kind: string, progress: number | null = null): JobRow => ({ id, kind, state: "running", progress });

describe("parseJobEvent", () => {
  it("reads the /api/jobs/events payload", () => {
    expect(parseJobEvent('{"type":"job","id":"j1","state":"running","progress":0.4,"message":null}')).toEqual({ id: "j1", state: "running", progress: 0.4 });
  });
  it("drops a non-finite progress rather than inventing one", () => {
    expect(parseJobEvent('{"id":"j1","state":"running","progress":"half"}')).toEqual({ id: "j1", state: "running", progress: null });
  });
  it("rejects garbage, a missing id and a missing state", () => {
    expect(parseJobEvent("not json")).toBeNull();
    expect(parseJobEvent('{"state":"running"}')).toBeNull();
    expect(parseJobEvent('{"id":"j1"}')).toBeNull();
    expect(parseJobEvent("[1]")).toBeNull();
  });
});

describe("applyJobEvent", () => {
  const held = [run("a", "risk.sweep", 0.1), run("b", "ingest.fred")];
  it("moves a known running job's progress without a list read", () => {
    const r = applyJobEvent(held, { id: "a", state: "running", progress: 0.6 });
    expect(r.needsList).toBe(false);
    expect(r.jobs.find((j) => j.id === "a")?.progress).toBe(0.6);
    expect(held[0].progress).toBe(0.1); // immutable
  });
  it("returns the same array when nothing changed", () => {
    const r = applyJobEvent(held, { id: "a", state: "running", progress: 0.1 });
    expect(r.jobs).toBe(held);
    expect(r.needsList).toBe(false);
  });
  it("keeps the held progress when the event carries none", () => {
    expect(applyJobEvent(held, { id: "a", state: "running", progress: null }).jobs).toBe(held);
  });
  it("removes a job that left running (done, failed, cancelled, requeued)", () => {
    for (const state of ["done", "failed", "cancelled", "queued"]) {
      const r = applyJobEvent(held, { id: "b", state, progress: null });
      expect(r.jobs.map((j) => j.id)).toEqual(["a"]);
      expect(r.needsList).toBe(false);
    }
  });
  it("asks for a list read when an unknown job starts: the event has no kind, and a lamp is never guessed", () => {
    const r = applyJobEvent(held, { id: "c", state: "running", progress: 0 });
    expect(r.needsList).toBe(true);
    expect(r.jobs).toBe(held);
  });
  it("ignores an unknown job that is not running", () => {
    const r = applyJobEvent(held, { id: "z", state: "done", progress: 1 });
    expect(r).toEqual({ jobs: held, needsList: false });
  });
});

describe("reconnectDelay", () => {
  it("backs off 1, 2, 4 … seconds and caps at 60 s", () => {
    expect([0, 1, 2, 3].map(reconnectDelay)).toEqual([1000, 2000, 4000, 8000]);
    expect(reconnectDelay(20)).toBe(60_000);
    expect(reconnectDelay(-1)).toBe(1000);
  });
});

describe("jobsPollInterval", () => {
  it("polls slowly to reconcile while the stream is live, at 15 s while it is not, 5 min after errors", () => {
    expect(jobsPollInterval("live", false)).toBe(JOBS_POLL_LIVE);
    expect(jobsPollInterval("connecting", false)).toBe(JOBS_POLL_DOWN);
    expect(jobsPollInterval("down", false)).toBe(JOBS_POLL_DOWN);
    expect(jobsPollInterval("live", true)).toBe(JOBS_POLL_ERROR);
    expect(JOBS_POLL_DOWN).toBe(15_000);
    expect(JOBS_POLL_LIVE).toBeGreaterThan(JOBS_POLL_DOWN);
  });
});

describe("readingInterval", () => {
  it("takes one reading per quote-cache refresh while the session is open", () => {
    expect(readingInterval({ clock: { is_open: true }, quality: { quote_cache_s: 60 } })).toBe(60_000);
  });
  it("never polls faster than 15 s nor slower than 5 min while open", () => {
    expect(readingInterval({ clock: { is_open: true }, quality: { quote_cache_s: 2 } })).toBe(15_000);
    expect(readingInterval({ clock: { is_open: true }, quality: { quote_cache_s: 3600 } })).toBe(300_000);
  });
  it("falls back to 15 s while open when the reading states no quote cache", () => {
    expect(readingInterval({ clock: { is_open: true } })).toBe(15_000);
    expect(readingInterval({ clock: { is_open: true }, quality: { quote_cache_s: Number.NaN } })).toBe(15_000);
  });
  it("is 5 min while the session is closed or before the first reading", () => {
    expect(readingInterval({ clock: { is_open: false }, quality: { quote_cache_s: 60 } })).toBe(300_000);
    expect(readingInterval(undefined)).toBe(300_000);
  });
});

describe("mcVarOf", () => {
  const wire = { var_usd: 12_345.6, alpha: 0.99, horizon_days: 1, method: "fhs", paths: 250_000, as_of: "2026-10-07T14:30:00Z", artifact_id: "j9" };
  it("reads the intraday Monte Carlo VaR the reading carries (M2)", () => {
    expect(mcVarOf({ mc_var: wire })).toEqual({ var_usd: 12_345.6, alpha: 0.99, horizon_days: 1, method: "fhs", paths: 250_000, as_of: "2026-10-07T14:30:00Z", artifact_id: "j9" });
  });
  it("is null when the reading has none — absent, never zero", () => {
    expect(mcVarOf({})).toBeNull();
    expect(mcVarOf({ mc_var: null })).toBeNull();
    expect(mcVarOf(undefined)).toBeNull();
  });
  it("refuses a value without its asOf, or a non-finite value", () => {
    expect(mcVarOf({ mc_var: { ...wire, as_of: "" } })).toBeNull();
    expect(mcVarOf({ mc_var: { ...wire, as_of: undefined } })).toBeNull();
    expect(mcVarOf({ mc_var: { ...wire, var_usd: Number.POSITIVE_INFINITY } })).toBeNull();
    expect(mcVarOf({ mc_var: { ...wire, var_usd: "12" } })).toBeNull();
  });
  it("keeps optional fields null when the artifact omits them", () => {
    expect(mcVarOf({ mc_var: { var_usd: 1, as_of: "2026-10-07T14:30:00Z" } })).toEqual({ var_usd: 1, alpha: null, horizon_days: null, method: null, paths: null, as_of: "2026-10-07T14:30:00Z", artifact_id: null });
  });
});

describe("mcLabel / mcIsStale", () => {
  const m = { var_usd: 1, alpha: 0.99, horizon_days: 1, method: "fhs", paths: null, as_of: "2026-10-07T14:30:00Z", artifact_id: null };
  it("labels, not sentences", () => {
    expect(mcLabel(m)).toBe("MC VAR 99 · 1D · FHS");
    expect(mcLabel({ ...m, alpha: null, horizon_days: null, method: null })).toBe("MC VAR");
    expect(mcLabel({ ...m, alpha: 0.975, method: "t-copula" })).toBe("MC VAR 97.5 · 1D · T-COPULA");
  });
  it("goes stale after one missed 15-minute run plus slack", () => {
    const t = Date.parse(m.as_of);
    expect(mcIsStale(m, t + 20 * 60_000)).toBe(false);
    expect(mcIsStale(m, t + MC_MAX_AGE_S * 1000 + 1)).toBe(true);
  });
  it("an unparseable or future asOf is stale, never fresh", () => {
    expect(mcIsStale({ ...m, as_of: "yesterday" }, Date.now())).toBe(true);
    expect(mcIsStale(m, Date.parse(m.as_of) - 10 * 60_000)).toBe(true);
  });
});
