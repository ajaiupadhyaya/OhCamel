import { describe, expect, it } from "vitest";
import { ApiError, DataUnavailableError, NetworkError } from "./api";
import { absentLabel, manifestOf } from "./artifacts";

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
  it("rejects anything without an id", () => {
    expect(manifestOf(null)).toBeNull();
    expect(manifestOf({})).toBeNull();
    expect(manifestOf({ manifest: {} })).toBeNull();
  });
});
