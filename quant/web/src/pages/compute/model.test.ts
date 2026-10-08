import { describe, expect, it } from "vitest";
import { applyEvent, hostSeries, kernelRows, last24h, measuredOn, pageOf, sliceLabel, type JobRow } from "./model";

const job = (over: Partial<JobRow>): JobRow => ({ id: "J1", kind: "risk.mc_atlas", state: "done", submitted_at: "2026-10-07T01:00:00Z", ...over });

describe("pageOf: every artifact links to the page that reads it", () => {
  it("maps each Lane M product kind to its page", () => {
    expect(pageOf("risk.mc_atlas")).toEqual({ to: "/risk", code: "RISK" });
    expect(pageOf("risk.mc_intraday")).toEqual({ to: "/risk", code: "RISK" });
    expect(pageOf("vol.forecast_league")?.to).toBe("/options?tab=forecasts");
    expect(pageOf("vol.surface_history")?.to).toBe("/options?tab=history");
    expect(pageOf("cov.league")?.to).toBe("/portfolio?tab=cov");
    expect(pageOf("farm.sweep")?.to).toBe("/research?view=farm");
    expect(pageOf("models.xs_lgbm")?.to).toBe("/research?view=models");
    expect(pageOf("regime.hmm")?.to).toBe("/macro?tab=regimes");
  });
  it("sends ingest kinds to the data freshness they feed, and has no page for API or self-test jobs", () => {
    expect(pageOf("ingest.bars_daily")).toEqual({ to: "/compute?kind=ingest.bars_daily", code: "DATA" });
    expect(pageOf("api.backtest_sweep")).toBeNull();
    expect(pageOf("ops.selftest")).toBeNull();
    expect(pageOf("nonsense")).toBeNull();
  });
});

describe("sliceLabel", () => {
  it("shortens cgroup slice names", () => {
    expect(sliceLabel("ohcamel-rt.slice")).toBe("RT");
    expect(sliceLabel("ohcamel-batch.slice")).toBe("BATCH");
    expect(sliceLabel("system.slice")).toBe("SYSTEM");
  });
});

describe("hostSeries: hostd history into chart columns", () => {
  const host = {
    cpus: 2,
    latest: { mem_total: 4e9 },
    history: [
      { t_ms: 1000, cpu: 0.5, steal: 0.01, mem_available: 3e9, groups_cpu: { "ohcamel-rt.slice": 0.1, "ohcamel-batch.slice": 0.8 } },
      { t_ms: 2000, cpu: 0.25, steal: null, mem_available: 2.5e9, groups_cpu: { "ohcamel-rt.slice": null } },
    ],
  };
  it("gives total CPU in cores, one series per slice, steal and memory used", () => {
    const s = hostSeries(host)!;
    expect(s.t).toEqual([1000, 2000]);
    expect(s.minAgo).toEqual([-1000 / 60_000, 0]);
    expect(s.cpuCores).toEqual([1, 0.5]);
    expect(s.slices.map((x) => x.name)).toEqual(["RT", "BATCH"]);
    expect(s.slices[1].y).toEqual([0.8, null]);
    expect(s.steal).toEqual([0.01, null]);
    expect(s.memUsedGb).toEqual([1, 1.5]);
  });
  it("is null without history, and leaves memory used missing without mem_total (never a guess)", () => {
    expect(hostSeries({ cpus: 2, latest: null, history: [] })).toBeNull();
    expect(hostSeries({ history: [{ t_ms: 1, cpu: 0.5, mem_available: 1 }] })!.memUsedGb).toEqual([null]);
    expect(hostSeries({ history: [{ t_ms: 1, cpu: 0.5 }] })!.cpuCores).toEqual([null]);
  });
});

describe("last24h", () => {
  it("keeps jobs finished in the last 24 hours, newest first", () => {
    const now = Date.parse("2026-10-07T12:00:00Z");
    const rows = [
      job({ id: "a", finished_at: "2026-10-07T02:00:00Z" }),
      job({ id: "b", finished_at: "2026-10-06T11:00:00Z" }),
      job({ id: "c", finished_at: "2026-10-07T10:00:00Z", state: "failed" }),
      job({ id: "d", finished_at: null, state: "running" }),
    ];
    expect(last24h(rows, now).map((r) => r.id)).toEqual(["c", "a"]);
  });
});

describe("applyEvent: the SSE stream updates the job list in place", () => {
  const rows = [job({ id: "r", state: "running", progress: 0.1, message: "fit" })];
  it("updates progress and message of a known job and asks for no refetch", () => {
    const out = applyEvent(rows, { type: "job", id: "r", state: "running", progress: 0.6, message: "paths" });
    expect(out.rows[0]).toMatchObject({ progress: 0.6, message: "paths", state: "running" });
    expect(out.refetch).toBe(false);
    expect(rows[0].progress).toBe(0.1);
  });
  it("asks for a refetch on an unknown job or a state change", () => {
    expect(applyEvent(rows, { type: "job", id: "new", state: "queued", progress: null, message: null }).refetch).toBe(true);
    const done = applyEvent(rows, { type: "job", id: "r", state: "done", progress: 1, message: null });
    expect(done.refetch).toBe(true);
    expect(done.rows[0].state).toBe("done");
  });
  it("keeps the last progress when an event carries none", () => {
    expect(applyEvent(rows, { type: "job", id: "r", state: "running", progress: null, message: null }).rows[0].progress).toBe(0.1);
  });
});

describe("kernelRows", () => {
  const body = {
    kernels: [
      { name: "svi_fit", python_ms: 16, rust_1t_ms: 0.5, rust_2t_ms: null, measured_on: "laptop", sha: "55d9c96258a3" },
      { name: "fhs_paths", python_ms: 60, rust_1t_ms: 40, rust_2t_ms: 20, measured_on: "laptop", sha: "55d9c96258a3" },
      { name: "garch_fit", python_ms: 3, rust_1t_ms: 20, rust_2t_ms: null, measured_on: "droplet", sha: "x" },
    ],
    engines: { svi_fit: "rust", fhs_paths: "python" },
  };
  it("computes the 1-thread speed-up and 2-thread scaling, and the engine each kernel runs on here", () => {
    const r = kernelRows(body);
    expect(r[0]).toMatchObject({ name: "svi_fit", speedup: 32, scaling: null, engine: "rust" });
    expect(r[1]).toMatchObject({ speedup: 1.5, scaling: 2, engine: "python" });
    expect(r[2]).toMatchObject({ speedup: 0.15, engine: null });
  });
  it("is empty for a body without kernels", () => {
    expect(kernelRows({})).toEqual([]);
    expect(kernelRows(null)).toEqual([]);
  });
  it("lists the distinct machines the table was measured on", () => {
    expect(measuredOn(kernelRows(body))).toEqual(["laptop", "droplet"]);
  });
});

describe("parseJobEvent", () => {
  it("accepts a well-formed job event and rejects anything else", async () => {
    const { parseJobEvent } = await import("./model");
    expect(parseJobEvent('{"type":"job","id":"A","state":"running","progress":0.5,"message":"x"}')).toEqual({ type: "job", id: "A", state: "running", progress: 0.5, message: "x" });
    expect(parseJobEvent('{"type":"job","id":"A","state":"done"}')).toEqual({ type: "job", id: "A", state: "done", progress: null, message: null });
    expect(parseJobEvent("not json")).toBeNull();
    expect(parseJobEvent('{"type":"host"}')).toBeNull();
    expect(parseJobEvent('{"type":"job","id":7,"state":"x"}')).toBeNull();
  });
});
