import { describe, expect, it } from "vitest";
import { IV_STATUS_TEXT, ivStatusInfo } from "./ChainTable";

describe("blank IV statuses", () => {
  it("labels every non-ok status", () => {
    for (const s of ["no_quote", "zero_bid", "crossed", "wide", "arb_bound", "at_intrinsic", "below_model_forward", "no_vol"] as const)
      expect(IV_STATUS_TEXT[s][0].length).toBeGreaterThan(0);
  });
  it("explains a status with the chain's own note when it has one, else the static text", () => {
    const notes = ["quotes flagged 'wide' when ...", "below_model_forward: 251 calls, 0 puts have a mid below D(F - K)+ ..."];
    expect(ivStatusInfo("below_model_forward", notes).text).toBe(notes[1]);
    expect(ivStatusInfo("at_intrinsic", notes).text).toBe(IV_STATUS_TEXT.at_intrinsic[1]);
    expect(ivStatusInfo("wide", undefined).title).toBe("No IV: wide");
  });
});
