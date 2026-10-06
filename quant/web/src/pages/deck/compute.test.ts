import { describe, expect, it } from "vitest";
import { computeBank, kindLegend, type HostWire, type JobsWire } from "./compute";

const host: HostWire = {
  version: 1,
  interval_s: 5,
  cpus: 2,
  now_ms: 1_700_000_000_000,
  latest: { t_ms: 1_700_000_000_000, cpu: 0.42, steal: 0.031, iowait: 0, load1: 0.8, mem_total: 4_000_000_000, mem_available: 1_000_000_000, swap_used: 0, groups: {} },
};
const jobs = (...rows: [string, number | null][]): JobsWire => ({
  jobs: rows.map(([kind, progress], i) => ({ id: `j${i}`, kind, state: "running", progress, started_at: "2026-10-06T20:00:00Z" })),
});

describe("computeBank", () => {
  it("reads CPU, steal and memory from hostd as fractions with levels", () => {
    const b = computeBank({ host, jobs: jobs() });
    expect(b.host).toBe("ok");
    expect(b.bars.map((x) => [x.key, x.value, x.text, x.level])).toEqual([
      ["cpu", 0.42, "42%", "ok"],
      ["steal", 0.031, "3.1%", "ok"],
      ["mem", 0.75, "75%", "near"],
    ]);
  });

  it("marks a bar over at 90 % and clamps the fill to [0, 1]", () => {
    const b = computeBank({ host: { ...host, latest: { ...host.latest!, cpu: 1.2, steal: -0.1, mem_available: 100_000_000 } }, jobs: jobs() });
    expect(b.bars[0]).toMatchObject({ value: 1.2, fill: 1, level: "over" });
    expect(b.bars[1]).toMatchObject({ fill: 0 });
    expect(b.bars[2].level).toBe("over");
  });

  it("never invents a host reading: unconfigured, down and warming are distinct and bars are unknown", () => {
    for (const [input, state] of [
      [{ hostError: { configured: false } }, "off"],
      [{ hostError: { configured: true } }, "down"],
      [{ host: { ...host, latest: null } }, "warming"],
    ] as const) {
      const b = computeBank({ ...input, jobs: jobs() });
      expect(b.host).toBe(state);
      expect(b.bars.every((x) => x.value == null && x.level === "unknown" && x.text === "—")).toBe(true);
    }
  });

  it("treats a missing or non-finite memory field as unknown, not as zero", () => {
    const b = computeBank({ host: { ...host, latest: { ...host.latest!, mem_total: 0, cpu: Number.NaN } }, jobs: jobs() });
    expect(b.bars[0].level).toBe("unknown");
    expect(b.bars[2].level).toBe("unknown");
  });

  it("lights one lamp per running job kind, sorted, with counts and the least progress", () => {
    const b = computeBank({ host, jobs: jobs(["risk.atlas", 0.5], ["farm.sweep", null], ["risk.atlas", 0.2]) });
    expect(b.jobs).toBe("ok");
    expect(b.lamps).toEqual([
      { kind: "farm.sweep", legend: "FARM SWEEP", count: 1, progress: null },
      { kind: "risk.atlas", legend: "RISK ATLAS", count: 2, progress: 0.2 },
    ]);
  });

  it("ignores rows that are not running (the endpoint is filtered, but the lamps must not lie)", () => {
    const w: JobsWire = { jobs: [{ id: "a", kind: "x.y", state: "done" }, { id: "b", kind: "x.y", state: "running" }] };
    expect(computeBank({ host, jobs: w }).lamps).toEqual([{ kind: "x.y", legend: "X Y", count: 1, progress: null }]);
  });

  it("reports the jobs read as down when it fails, and as unknown before it answers", () => {
    expect(computeBank({ host, jobsError: true }).jobs).toBe("down");
    expect(computeBank({ host }).jobs).toBe("unknown");
    expect(computeBank({ host, jobsError: true }).lamps).toEqual([]);
  });

  it("summarises for screen readers", () => {
    const b = computeBank({ host, jobs: jobs(["risk.atlas", 0.5]) });
    expect(b.summary).toBe("Compute: CPU 42%, steal 3.1%, memory 75%; 1 job running: RISK ATLAS.");
    expect(computeBank({ hostError: { configured: false }, jobs: jobs() }).summary).toBe("Compute: host telemetry not configured; no jobs running.");
  });
});

describe("kindLegend", () => {
  it("prints a kind as uppercase words, cut to 14 characters", () => {
    expect(kindLegend("api.risk_var")).toBe("API RISK VAR");
    expect(kindLegend("warehouse.bars_minute")).toBe("WAREHOUSE BAR…");
    expect(kindLegend("")).toBe("?");
  });
});
