import { describe, expect, it } from "vitest";
import { ApiError, DataUnavailableError, NetworkError } from "../../lib/api";
import type { Manifest } from "../../lib/artifacts";
import { productStatus } from "./status";

const m = (over: Partial<Manifest> = {}): Manifest => ({ id: "01X", kind: "risk.mc_atlas", ...over });

describe("productStatus: a product row's live state, read from its latest artifact (no claim ahead of it)", () => {
  it("leads with the verdict and the data date", () => {
    expect(productStatus(m({ verdict: "DESCRIPTIVE ONLY", data_asof: "2026-10-07" }), null, false))
      .toEqual({ text: "DESCRIPTIVE ONLY · ASOF 07 OCT 2026", fail: false });
  });
  it("marks FAIL for the signal colour and STALE when the API says so", () => {
    expect(productStatus(m({ verdict: "FAIL", data_asof: "2026-10-01", stale: true }), null, false))
      .toEqual({ text: "FAIL · ASOF 01 OCT 2026 · STALE", fail: true });
  });
  it("says NO VERDICT and a dash when the manifest has neither", () => {
    expect(productStatus(m(), null, false).text).toBe("NO VERDICT · ASOF —");
  });
  it("says NOT YET RUN on a 404 and DATA UNAVAILABLE on a 503", () => {
    expect(productStatus(null, new ApiError(404, "no artifact", "/artifacts/x/latest"), false))
      .toEqual({ text: "NOT YET RUN", fail: false });
    expect(productStatus(null, new DataUnavailableError("down", "/artifacts/x/latest"), false).text).toBe("DATA UNAVAILABLE");
  });
  it("says UNREADABLE for any other failure, and a dash while reading", () => {
    expect(productStatus(null, new NetworkError("offline", "/artifacts/x/latest"), false).text).toBe("UNREADABLE");
    expect(productStatus(null, new ApiError(500, "boom", "/artifacts/x/latest"), false).text).toBe("UNREADABLE");
    expect(productStatus(null, null, true).text).toBe("—");
  });
});
