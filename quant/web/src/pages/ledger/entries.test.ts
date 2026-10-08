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

describe("Ledger accuracy (re-dated 2026-10-07, Ship plan P2-7)", () => {
  it("counts the kernels the Methodology documents", async () => {
    const { KERNEL_API } = await import("../methodology/compute");
    expect(KERNEL_API).toHaveLength(10);
    expect(LEDGER.find((e) => e.item === "KERNELS")?.detail).toMatch(/^Ten Rust kernels/);
  });
  it("has one row per item, and every section non-empty", async () => {
    const { SECTION_ORDER } = await import("./entries");
    const items = LEDGER.map((e) => `${e.section}:${e.item}`);
    expect(new Set(items).size).toBe(items.length);
    for (const s of SECTION_ORDER) expect(LEDGER.some((e) => e.section === s), s).toBe(true);
  });
  it("is dated by an ISO date", async () => {
    const { LEDGER_AS_OF } = await import("./entries");
    expect(LEDGER_AS_OF).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
