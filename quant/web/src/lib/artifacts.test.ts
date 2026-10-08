import { describe, expect, it } from "vitest";
import { ApiError, DataUnavailableError, NetworkError } from "./api";
import { KINDS, PREREGISTERED, absentLabel, artifactMaxAge, artifactStamp, frameRecords, manifestOf } from "./artifacts";

describe("absentLabel (missing reads render as missing)", () => {
  it("a 404 means the job kind has not produced an artifact yet", () => {
    expect(absentLabel(new ApiError(404, "no artifact", "/api/artifacts/risk.mc_atlas/latest"))).toBe("NOT YET RUN");
  });
  it("a 503 is a source outage", () => {
    expect(absentLabel(new DataUnavailableError("hostd_unavailable", "/api/ops/host"))).toBe("DATA UNAVAILABLE");
  });
  it("other failures are not absence: the caller shows the error", () => {
    expect(absentLabel(new ApiError(500, "boom", "/x"))).toBeNull();
    expect(absentLabel(new NetworkError("down", "/x"))).toBeNull();
    expect(absentLabel(new Error("x"))).toBeNull();
    expect(absentLabel(undefined)).toBeNull();
  });
});

describe("manifestOf", () => {
  const m = { id: "a1", kind: "risk.mc_atlas", data_asof: "2026-10-05", finished_at: "2026-10-05T06:00:00Z" };
  it("accepts a bare manifest", () => expect(manifestOf(m)?.id).toBe("a1"));
  it("accepts Lane B's {manifest, stale}", () => {
    const out = manifestOf({ manifest: m, stale: true });
    expect(out?.id).toBe("a1");
    expect(out?.stale).toBe(true);
  });
  it("reads Lane M's latest payload: verdict and verdict_detail first, then the manifest", () => {
    const out = manifestOf({ verdict: "FAIL", verdict_detail: "0 PASS · 3 FAIL", stale: false, manifest: { ...m, verdict: "FAIL", verdict_detail: "0 PASS · 3 FAIL" } });
    expect([out?.verdict, out?.verdict_detail, out?.stale]).toEqual(["FAIL", "0 PASS · 3 FAIL", false]);
  });
  it("takes the top-level verdict when the manifest inside lacks one", () => {
    const out = manifestOf({ verdict: "DESCRIPTIVE ONLY", verdict_detail: "risk measurement", manifest: m });
    expect([out?.verdict, out?.verdict_detail]).toEqual(["DESCRIPTIVE ONLY", "risk measurement"]);
  });
  it("rejects anything without an id", () => {
    expect(manifestOf(null)).toBeNull();
    expect(manifestOf({})).toBeNull();
    expect(manifestOf({ manifest: {} })).toBeNull();
  });
});

describe("frameRecords (artifact tables arrive as lib/serialize.frame)", () => {
  it("turns a column-major frame into rows", () => {
    const f = { index: [0, 1], columns: ["book", "var"], data: { book: ["core", "spy"], var: [0.02, null] } };
    expect(frameRecords(f)).toEqual([
      { book: "core", var: 0.02 },
      { book: "spy", var: null },
    ]);
  });
  it("rejects anything that is not a frame", () => {
    expect(frameRecords(null)).toBeNull();
    expect(frameRecords({ columns: ["a"] })).toBeNull();
    expect(frameRecords({ index: [0], columns: ["a"], data: {} })).toBeNull();
  });
  it("an empty frame is zero rows, not missing", () => {
    expect(frameRecords({ index: [], columns: ["a"], data: { a: [] } })).toEqual([]);
  });
});

describe("artifactMaxAge (Review Focus 1: the API's stale flag wins over the page's clock)", () => {
  it("stale: true is always stale, stale: false is never stale, absent falls back to a missed night", () => {
    expect(artifactMaxAge({ id: "a", kind: "k", stale: true })).toBeLessThan(0);
    expect(artifactMaxAge({ id: "a", kind: "k", stale: false })).toBe(Infinity);
    expect(artifactMaxAge({ id: "a", kind: "k" })).toBe(36 * 3600);
  });
});

describe("PREREGISTERED", () => {
  it("EXP-Q01 and EXP-Q02 were approved by the owner on 2026-10-06, so their results are read", () => {
    expect(PREREGISTERED[KINDS.models]).toBe(true);
    expect(PREREGISTERED[KINDS.regimes]).toBe(true);
  });
});

describe("artifactStamp (a run time is never shown as a data date)", () => {
  it("data_asof reads AS OF", () => {
    expect(artifactStamp({ data_asof: "2026-06-01", finished_at: "2026-10-08T02:10:00Z" })).toEqual({ at: "2026-06-01", label: "AS OF" });
  });
  it("without data_asof the finish time reads RUN", () => {
    expect(artifactStamp({ data_asof: null, finished_at: "2026-10-08T02:10:00Z" })).toEqual({ at: "2026-10-08T02:10:00Z", label: "RUN" });
  });
  it("neither: nothing", () => expect(artifactStamp({})).toEqual({ at: undefined, label: "AS OF" }));
});
