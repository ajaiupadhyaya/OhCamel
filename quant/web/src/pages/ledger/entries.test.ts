import { describe, expect, it } from "vitest";
import { LEDGER } from "./entries";

describe("Ledger entries", () => {
  it("marks the nightly products P1-P7 as pending their first run (no claim ahead of the artifacts)", () => {
    const products = LEDGER.filter((e) => /^P[1-7] /.test(e.item));
    expect(products).toHaveLength(7);
    for (const e of products) expect(e.pending, e.item).toBe(true);
  });
  it("leaves the shipped routes unmarked", () => {
    expect(LEDGER.find((e) => e.item === "FRONT")?.pending).toBeFalsy();
  });
});
